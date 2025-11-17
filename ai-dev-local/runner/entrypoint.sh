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

slugify() {
  echo "$1" | tr '[:upper:]' '[:lower:]' | tr -cs 'a-z0-9' '-' | sed -E 's/^-+//; s/-+$//'
}

SUMMARY_SLUG=$(slugify "${ISSUE_SUMMARY}")
SUMMARY_SLUG=${SUMMARY_SLUG:-update}
BRANCH_NAME="ai/${ISSUE_KEY}-${SUMMARY_SLUG}"

BASE_BRANCH=${BASE_BRANCH:-"main"}
export BRANCH_NAME BASE_BRANCH ISSUE_KEY ISSUE_SUMMARY

echo "[runner] Checking out branch ${BRANCH_NAME}"
git checkout -B "${BRANCH_NAME}"

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

if git diff --quiet; then
  echo "[runner] No changes detected; skipping commit/push."
else
  git add -A
  COMMIT_MSG="AI work for ${ISSUE_KEY}: ${ISSUE_SUMMARY}"
  git commit -m "${COMMIT_MSG}"
  git push origin "${BRANCH_NAME}"
  echo "[runner] Changes committed and pushed to ${BRANCH_NAME}"

  REPO_SLUG=${REPO_URL#https://github.com/}
  REPO_SLUG=${REPO_SLUG%.git}
  REPO_OWNER=${REPO_SLUG%%/*}

  echo "[runner] Checking for existing PR..."
  existing_pr=$(curl -sS \
    -H "Authorization: token ${GH_TOKEN}" \
    -H "Accept: application/vnd.github+json" \
    "https://api.github.com/repos/${REPO_SLUG}/pulls?head=${REPO_OWNER}:${BRANCH_NAME}")

  if echo "${existing_pr}" | grep -q "\"head\""; then
    echo "[runner] PR already exists for ${BRANCH_NAME}, skipping creation."
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
      pr_url=$(echo "${response}" | python3 -c "import sys, json; print(json.load(sys.stdin)['html_url'])")
      echo "[runner] Draft PR created: ${pr_url}"
    else
      echo "[runner] Failed to create PR. Response:"
      echo "${response}"
    fi
  fi
fi

echo "[runner] Completed successfully."

