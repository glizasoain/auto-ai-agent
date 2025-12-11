#!/usr/bin/env bash
set -euo pipefail

echo "[runner] Starting autonomous Aider agent..."

git config --global user.email "cursor-bot@example.com"
git config --global user.name "cursor-bot"

if [ -z "${REPO_URL:-}" ]; then
  echo "[runner] Missing REPO_URL"; exit 1
fi

if [ -z "${OPENAI_API_KEY:-}" ]; then
  echo "[runner] Missing OPENAI_API_KEY"; exit 1
fi

git clone "https://x-access-token:${GH_TOKEN}@${REPO_URL#https://}" repo
cd repo

REPO_SLUG=${REPO_URL#https://github.com/}
REPO_SLUG=${REPO_SLUG%.git}
REPO_OWNER=${REPO_SLUG%%/*}
export REPO_SLUG

ISSUE_KEY=${ISSUE_KEY:-"UNKNOWN"}
ISSUE_SUMMARY=${ISSUE_SUMMARY:-"No summary provided"}
EXISTING_PR_NUMBER=${PR_NUMBER:-""}

slugify() {
  local text
  text=$(echo "$1" | tr '[:upper:]' '[:lower:]' | tr -cs 'a-z0-9' '-' | sed -E 's/^-+//; s/-+$//')
  echo "${text:0:40}"
}

SESSION_BUCKET=${AIDER_SESSION_BUCKET:-}
SESSION_PREFIX_RAW=${AIDER_SESSION_PREFIX:-sessions/}
S3_ENDPOINT=${S3_ENDPOINT_URL:-}
PERSIST_SESSION=false
AWS_S3_ARGS=()
SESSION_FILE=""
SESSION_URI=""
SESSION_FINALIZED=false

if [ -n "$SESSION_BUCKET" ] && [ -n "${AWS_ACCESS_KEY_ID:-}" ] && [ -n "${AWS_SECRET_ACCESS_KEY:-}" ]; then
  PERSIST_SESSION=true
else
  echo "[runner] Session persistence disabled; set AIDER_SESSION_BUCKET and AWS credentials to enable it."
fi

if [ -n "$S3_ENDPOINT" ]; then
  AWS_S3_ARGS+=(--endpoint-url "$S3_ENDPOINT")
fi

if [ -n "${AWS_REGION:-}" ] && [ -z "${AWS_DEFAULT_REGION:-}" ]; then
  export AWS_DEFAULT_REGION="${AWS_REGION}"
elif [ -z "${AWS_DEFAULT_REGION:-}" ]; then
  export AWS_DEFAULT_REGION="us-east-1"
fi

SESSION_PREFIX=${SESSION_PREFIX_RAW#/}
if [ -n "$SESSION_PREFIX" ] && [ "${SESSION_PREFIX: -1}" != "/" ]; then
  SESSION_PREFIX="${SESSION_PREFIX}/"
fi

sanitize_session_component() {
  local value="$1"
  value=$(echo "$value" | tr '[:upper:]' '[:lower:]' | tr -cs 'a-z0-9' '-' | sed -E 's/^-+//; s/-+$//')
  echo "${value:-session}"
}

current_pr_url() {
  if [ -n "${FINAL_PR_URL:-}" ]; then
    echo "${FINAL_PR_URL}"
  elif [ -n "${PR_NUMBER:-}" ] && [ -n "${REPO_SLUG:-}" ]; then
    echo "https://github.com/${REPO_SLUG}/pull/${PR_NUMBER}"
  elif [ -n "${EXISTING_PR_NUMBER:-}" ] && [ -n "${REPO_SLUG:-}" ]; then
    echo "https://github.com/${REPO_SLUG}/pull/${EXISTING_PR_NUMBER}"
  else
    echo "pending"
  fi
}

session_metadata_block() {
  cat <<EOF
# === Session Metadata ===
# Issue: ${ISSUE_KEY}
# Summary: ${ISSUE_SUMMARY}
# Branch: ${BRANCH_NAME}
# PR: $1
# Last Updated: $(date -u +"%Y-%m-%dT%H:%M:%SZ")
# === End Metadata ===

EOF
}

strip_existing_metadata() {
  if [ ! -f "${SESSION_FILE}" ]; then
    return
  fi
  awk 'BEGIN{skip=0}
       /^# === Session Metadata ===$/{skip=1; next}
       /^# === End Metadata ===$/{skip=0; next}
       { if (skip==0) print }
      ' "${SESSION_FILE}"
}

prepare_session_persistence() {
  if [ "${PERSIST_SESSION}" != "true" ]; then
    return
  fi
  local key
  key=$(sanitize_session_component "${ISSUE_KEY}-${BRANCH_NAME}")
  SESSION_FILE="/tmp/aider-session-${key}.md"
  if [ -n "${SESSION_PREFIX}" ]; then
    SESSION_URI="s3://${SESSION_BUCKET}/${SESSION_PREFIX}${key}.md"
  else
    SESSION_URI="s3://${SESSION_BUCKET}/${key}.md"
  fi
  rm -f "${SESSION_FILE}"
  if aws s3 cp "${SESSION_URI}" "${SESSION_FILE}" "${AWS_S3_ARGS[@]}" --quiet 2>/dev/null; then
    echo "[runner] Restored Aider transcript from ${SESSION_URI}"
  else
    session_metadata_block "$(current_pr_url)" > "${SESSION_FILE}"
    echo "[runner] Initialized new Aider transcript at ${SESSION_URI}"
  fi
}

persist_session_transcript() {
  local cleanup="${1:-false}"
  if [ "${PERSIST_SESSION}" != "true" ] || [ -z "${SESSION_FILE}" ] || [ ! -f "${SESSION_FILE}" ]; then
    return
  fi
  local body_tmp
  body_tmp=$(mktemp)
  strip_existing_metadata > "${body_tmp}"
  session_metadata_block "$(current_pr_url)" > "${SESSION_FILE}"
  cat "${body_tmp}" >> "${SESSION_FILE}"
  rm -f "${body_tmp}"
  if aws s3 cp "${SESSION_FILE}" "${SESSION_URI}" "${AWS_S3_ARGS[@]}" --quiet; then
    echo "[runner] Uploaded transcript to ${SESSION_URI}"
  else
    echo "[runner] WARNING: Failed to upload transcript to ${SESSION_URI}"
  fi
  if [ "${cleanup}" = "true" ]; then
    SESSION_FINALIZED=true
    rm -f "${SESSION_FILE}"
  fi
}

BASE_BRANCH=${BASE_BRANCH:-"main"}
if [ -z "${BRANCH_NAME:-}" ]; then
  SUMMARY_SLUG=$(slugify "${ISSUE_SUMMARY}")
  SUMMARY_SLUG=${SUMMARY_SLUG:-update}
  RUN_ID=$(date -u +"%Y%m%d%H%M%S" | tail -c 7)
  BRANCH_NAME="ai/${ISSUE_KEY}/${SUMMARY_SLUG}-${RUN_ID}"
  echo "[runner] Creating new branch ${BRANCH_NAME}"
else
  echo "[runner] Reusing provided branch ${BRANCH_NAME}"
fi

export BRANCH_NAME BASE_BRANCH ISSUE_KEY ISSUE_SUMMARY

git fetch origin "${BASE_BRANCH}" --depth=1
if git ls-remote --exit-code origin "refs/heads/${BRANCH_NAME}" >/dev/null 2>&1; then
  echo "[runner] Branch ${BRANCH_NAME} exists on origin; checking out."
  git fetch origin "${BRANCH_NAME}:${BRANCH_NAME}"
  git checkout "${BRANCH_NAME}"
else
  echo "[runner] Branch ${BRANCH_NAME} does not exist; creating from ${BASE_BRANCH}."
  git checkout -B "${BRANCH_NAME}" "origin/${BASE_BRANCH}"
fi

if [ "${PERSIST_SESSION}" = "true" ]; then
  prepare_session_persistence
  trap 'if [ "${PERSIST_SESSION}" = "true" ] && [ "${SESSION_FINALIZED}" != "true" ]; then persist_session_transcript true; fi' EXIT
fi

# Check if REVIEW_FEEDBACK is set (indicates this is a follow-up run for PR review feedback)
if [ -n "${REVIEW_FEEDBACK:-}" ]; then
  echo "[runner] REVIEW_FEEDBACK is set (length: ${#REVIEW_FEEDBACK} chars)"
  echo "[runner] REVIEW_FEEDBACK preview: ${REVIEW_FEEDBACK:0:200}..."
  
  # Layer 1: Make review feedback the PRIMARY instruction with strong emphasis
  RUN_PROMPT="🚨 CRITICAL: This is a follow-up run to address reviewer feedback. A reviewer has requested specific changes to this PR.

═══════════════════════════════════════════════════════════════
REVIEWER'S REQUESTS (YOU MUST ADDRESS THESE - HIGHEST PRIORITY):
═══════════════════════════════════════════════════════════════
${REVIEW_FEEDBACK}
═══════════════════════════════════════════════════════════════

Your task:
1. Read and understand the reviewer's requests above
2. Implement ONLY the changes requested by the reviewer
3. DO NOT modify guardrails.yaml or .cursor/rules files under any circumstances
4. DO NOT work on the original Jira issue unless it's explicitly part of the reviewer's feedback
5. Focus exclusively on addressing the reviewer's feedback above

Original context (for reference only):
- Jira Issue: ${ISSUE_KEY}
- Summary: ${ISSUE_SUMMARY}
- Branch: ${BRANCH_NAME}

Follow coding standards from .cursor/rules and guardrails.yaml, but DO NOT edit those files."
  echo "[runner] Injected reviewer feedback as PRIMARY instruction."
else
  # Original prompt for new work (no review feedback)
  RUN_PROMPT="Work on Jira issue ${ISSUE_KEY}: ${ISSUE_SUMMARY}. Follow .cursor/rules (and guardrails.yaml), implement the necessary changes, add/modify tests, run tests, summarize work, and prepare a PR body linking the Jira issue. Branch to ${BRANCH_NAME}."
  echo "[runner] WARNING: REVIEW_FEEDBACK is NOT set in environment"
  echo "[runner] Available env vars starting with REVIEW: $(env | grep -i review || echo 'none')"
fi

echo "[runner] Launching aider with prompt:"
echo "${RUN_PROMPT}"

# Allow overriding default model via env (AIDER_MODEL). Fallback to gpt-4o-mini if unset.
DEFAULT_MODEL=${AIDER_MODEL:-"gpt-4o-mini"}
echo "[runner] Using Aider model: ${DEFAULT_MODEL}"

if ! command -v aider >/dev/null 2>&1; then
  echo "[runner] ERROR: 'aider' command not found in PATH."
  exit 1
fi

SESSION_FLAGS=()
if [ "${PERSIST_SESSION}" = "true" ]; then
  SESSION_FLAGS+=(--restore "${SESSION_FILE}" --save "${SESSION_FILE}")
fi

# Layer 4: Use Aider's --exclude to prevent editing guardrails and rules files
aider \
  --model "${DEFAULT_MODEL}" \
  "${SESSION_FLAGS[@]}" \
  --no-check-update \
  --yes \
  --exclude "guardrails.yaml" \
  --exclude ".cursor/rules/**" \
  --exclude ".cursor/**" \
  --message "${RUN_PROMPT}"

echo "[runner] Aider session completed."

persist_session_transcript false

if [ -n "${POST_RUN_COMMAND:-}" ]; then
  echo "[runner] Running post-run command: ${POST_RUN_COMMAND}"
  bash -lc "${POST_RUN_COMMAND}"
fi

git add -A
if git diff --cached --quiet; then
  echo "[runner] No newly staged changes; assuming Aider already committed."
else
  COMMIT_MSG="AI work for ${ISSUE_KEY}: ${ISSUE_SUMMARY}"
  git commit -m "${COMMIT_MSG}"
fi

git push -u origin "${BRANCH_NAME}"
echo "[runner] Branch ${BRANCH_NAME} pushed to origin."

FINAL_PR_NUMBER=""
FINAL_PR_URL=""

echo "[runner] Checking for existing PR..."
existing_pr=$(curl -sS \
  -H "Authorization: token ${GH_TOKEN}" \
  -H "Accept: application/vnd.github+json" \
  "https://api.github.com/repos/${REPO_SLUG}/pulls?head=${REPO_OWNER}:${BRANCH_NAME}")

existing_summary=$(printf '%s\n' "${existing_pr}" | python3 - <<'PY'
import json, sys
try:
    data = json.load(sys.stdin)
except json.JSONDecodeError:
    sys.exit(0)
if not isinstance(data, list) or not data:
    sys.exit(0)
pr = data[0]
print(f"{pr['number']} {pr['html_url']}")
PY
)

if [ -n "${existing_summary}" ]; then
  FINAL_PR_NUMBER=$(echo "${existing_summary}" | awk '{print $1}')
  FINAL_PR_URL=$(echo "${existing_summary}" | awk '{print $2}')
  echo "[runner] PR already exists for ${BRANCH_NAME}: ${FINAL_PR_URL}"
else
  PR_PAYLOAD=$(python3 - <<'PY'
import json, os
title = f"AI work for {os.environ.get('ISSUE_KEY', 'UNKNOWN')}: {os.environ.get('ISSUE_SUMMARY', 'No summary provided')}"
body = """## Summary
- Automated work by the Aider agent for {issue}
- Guardrails enforced via guardrails.yaml

## Notes
- Please review before merging.""".format(issue=os.environ.get('ISSUE_KEY', 'UNKNOWN'))
payload = {
    "title": title,
    "head": os.environ["BRANCH_NAME"],
    "base": os.environ.get("BASE_BRANCH", "main"),
    "body": body,
    "draft": True,
}
print(json.dumps(payload))
PY
)

  echo "[runner] Creating draft PR targeting ${BASE_BRANCH}..."
  response=$(curl -sS -X POST \
    -H "Authorization: token ${GH_TOKEN}" \
    -H "Accept: application/vnd.github+json" \
    https://api.github.com/repos/${REPO_SLUG}/pulls \
    -d "${PR_PAYLOAD}")

  if echo "${response}" | grep -q '"html_url"'; then
    FINAL_PR_NUMBER=$(echo "${response}" | python3 -c "import sys, json; print(json.load(sys.stdin)['number'])")
    FINAL_PR_URL=$(echo "${response}" | python3 -c "import sys, json; print(json.load(sys.stdin)['html_url'])")
    echo "[runner] Draft PR created: ${FINAL_PR_URL}"
  else
    echo "[runner] Failed to create PR. Response:"
    echo "${response}"
  fi
fi

RUNNER_RESULT=$(python3 - <<'PY'
import json, os
result = {
    "issue_key": os.environ.get("ISSUE_KEY", "UNKNOWN"),
    "branch_name": os.environ.get("BRANCH_NAME"),
    "repo_slug": os.environ.get("REPO_SLUG"),
}
pr_number = os.environ.get("FINAL_PR_NUMBER")
pr_url = os.environ.get("FINAL_PR_URL")
if pr_number:
    result["pr_number"] = int(pr_number)
if pr_url:
    result["pr_url"] = pr_url
print(json.dumps(result))
PY
)
echo "::runner-result::${RUNNER_RESULT}"

persist_session_transcript true

echo "[runner] Completed successfully."

