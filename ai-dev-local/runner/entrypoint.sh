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

ISSUE_KEY=${ISSUE_KEY:-"UNKNOWN"}
ISSUE_SUMMARY=${ISSUE_SUMMARY:-"No summary provided"}
EXISTING_PR_NUMBER=${PR_NUMBER:-""}

slugify() {
  local text
  text=$(echo "$1" | tr '[:upper:]' '[:lower:]' | tr -cs 'a-z0-9' '-' | sed -E 's/^-+//; s/-+$//')
  echo "${text:0:40}"
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

RUN_PROMPT="Work on Jira issue ${ISSUE_KEY}: ${ISSUE_SUMMARY}. Follow .cursor/rules (and guardrails.yaml), implement the necessary changes, add/modify tests, run tests, summarize work, and prepare a PR body linking the Jira issue. Branch to ${BRANCH_NAME}."

echo "[runner] Launching aider with prompt:"
echo "${RUN_PROMPT}"

# Allow overriding default model via env (AIDER_MODEL). Fallback to gpt-4o-mini if unset.
DEFAULT_MODEL=${AIDER_MODEL:-"gpt-4o-mini"}
echo "[runner] Using Aider model: ${DEFAULT_MODEL}"

if ! command -v aider >/dev/null 2>&1; then
  echo "[runner] ERROR: 'aider' command not found in PATH."
  exit 1
fi

aider \
  --model "${DEFAULT_MODEL}" \
  --no-check-update \
  --yes \
  --message "${RUN_PROMPT}"

echo "[runner] Aider session completed."

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

REPO_SLUG=${REPO_URL#https://github.com/}
REPO_SLUG=${REPO_SLUG%.git}
REPO_OWNER=${REPO_SLUG%%/*}
export REPO_SLUG

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

echo "[runner] Completed successfully."

