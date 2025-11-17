# PRD: Local Autonomous Coding Agent using Cursor + Prefect + Kubernetes

---

## 1. Overview

We are building a **local autonomous coding agent** that allows **Cursor** to act as a self-driving developer.

This system will:
- Select Jira (or GitHub) issues labeled **AI_ALLOWED**.
- Implement those issues end-to-end.
- Run and verify tests.
- Open Pull Requests (PRs) automatically.
- Interact with human reviewers until PRs can be merged.

This will be implemented locally using:
- **Prefect** (as the orchestrator),
- **Kind** (local Kubernetes cluster),
- **Cursor CLI** (headless development environment),
- **GitHub + Jira MCP servers** for integration.

Once validated, this local setup will serve as the foundation for production CI/CD automation.

---

## 2. Objectives

1. Build a **self-contained local environment** to simulate an enterprise-grade autonomous development loop.
2. Use Prefect as a **controller** to launch ephemeral Kubernetes pods that:
   - Run Cursor headlessly.
   - Query Jira or GitHub for eligible work.
   - Implement and test code.
   - Open PRs with full traceability.
3. Demonstrate full autonomy against a real repository (no dry-run fallback) while still running locally.
4. Transition to production Jira/GitHub integration once the local workflow is stable.

---

## 3. System Architecture

### 3.1 Components

| Component | Description |
|------------|-------------|
| **Prefect Flow** | Orchestrates job creation, monitoring, and cleanup |
| **Cursor Runner Pod** | Executes autonomous work as a headless developer |
| **Kind (Kubernetes)** | Provides local pod isolation and job control |
| **MCP Servers** | Expose GitHub and Jira APIs to Cursor |
| **Rules (Guardrails)** | Define allowed issue types and safe code boundaries |
| **Secrets & ConfigMaps** | Hold tokens, configuration, and policies securely |

---

## 4. Local Setup Prerequisites

Install core tools:

```bash
brew install kind kubectl node python git
pip install "prefect>=3.0.0" "prefect-kubernetes>=0.3.3"
```

Required credentials (stored as secrets):

| Variable | Description |
|-----------|-------------|
| `GH_TOKEN` | GitHub token with repo + PR permissions |
| `REPO_URL` | Target repository URL |
| `JIRA_BASE` | Jira instance base URL |
| `JIRA_EMAIL` | Bot account email |
| `JIRA_TOKEN` | Jira API token |

---

## 5. Directory Layout

```
ai-dev-local/
├─ k8s/
│  ├─ namespace.yaml
│  ├─ rbac.yaml
│  ├─ secrets.env
│  ├─ configmap-rules.yaml
│  ├─ configmap-mcp.json
├─ runner/
│  ├─ Dockerfile
│  ├─ entrypoint.sh
│  └─ .cursor/
│     ├─ rules/ai-allowed.mdc
│     └─ mcp.json
└─ flows/
   └─ cursor_flow.py
```

---

## 6. Kubernetes Configuration

### 6.1 Namespace and RBAC

```yaml
apiVersion: v1
kind: Namespace
metadata:
  name: ai-dev
---
apiVersion: v1
kind: ServiceAccount
metadata:
  name: job-launcher
  namespace: ai-dev
---
apiVersion: rbac.authorization.k8s.io/v1
kind: Role
metadata:
  name: job-manager
  namespace: ai-dev
rules:
  - apiGroups: ["", "batch"]
    resources: ["pods", "pods/log", "jobs"]
    verbs: ["create", "get", "list", "watch", "delete"]
---
apiVersion: rbac.authorization.k8s.io/v1
kind: RoleBinding
metadata:
  name: job-manager-binding
  namespace: ai-dev
subjects:
  - kind: ServiceAccount
    name: job-launcher
    namespace: ai-dev
roleRef:
  kind: Role
  name: job-manager
  apiGroup: rbac.authorization.k8s.io
```

---

## 7. Prefect Flow Definition

**File:** `flows/cursor_flow.py`

```python
from prefect import flow, task, get_run_logger
from prefect_kubernetes import KubernetesJob

NAMESPACE = "ai-dev"

@task
def select_issue() -> dict:
    log = get_run_logger()
    # TODO: query Jira/GitHub for real issues labeled AI_ALLOWED
    return {"issue_key": "PAY-123", "issue_summary": "Example payment issue"}

@flow(name="cursor-agent-local")
def run_cursor_agent():
    log = get_run_logger()
    meta = select_issue()

    job = KubernetesJob(
        name="cursor-runner-job",
        namespace=NAMESPACE,
        image="cursor-runner:local",
        env={
            "ISSUE_KEY": meta["issue_key"],
            "ISSUE_SUMMARY": meta["issue_summary"],
        },
        image_pull_policy="IfNotPresent",
        restart_policy="Never",
        service_account_name="job-launcher",
        pod_spec=dict(
            containers=[
                dict(
                    name="runner",
                    image="cursor-runner:local",
                    envFrom=[{"secretRef": {"name": "aider-secrets"}}],
                )
            ]
        ),
        stream_output=True,
        delete_job_after_completion=True,
    )

    log.info("Submitting Cursor job...")
    result = job.run()
    log.info(f"Job result: {result}")

if __name__ == "__main__":
    run_cursor_agent()
```

---

## 8. Cursor Runner

### 8.1 Dockerfile

**File:** `runner/Dockerfile`

```dockerfile
FROM node:20-bullseye

RUN apt-get update && apt-get install -y git curl bash ca-certificates python3 python3-pip && rm -rf /var/lib/apt/lists/*
RUN pip3 install --no-cache-dir aider-chat

WORKDIR /workspace
COPY .cursor /workspace/.cursor
COPY entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod +x /usr/local/bin/entrypoint.sh

ENV GH_TOKEN=""
ENV REPO_URL=""
ENV JIRA_BASE=""
ENV JIRA_EMAIL=""
ENV JIRA_TOKEN=""

ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
```

---

### 8.2 Entrypoint Script

**File:** `runner/entrypoint.sh`

```bash
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

git checkout -B "${BRANCH_NAME}"

RUN_PROMPT="Work on Jira issue ${ISSUE_KEY}: ${ISSUE_SUMMARY}. Follow .cursor/rules, add/modify tests, run tests, summarize the work, and prepare a PR body linking the Jira issue."

aider \
  --model "${AIDER_MODEL:-openai/gpt-4o-mini}" \
  --no-check-update \
  --yes \
  --message "${RUN_PROMPT}"

if git diff --quiet; then
  echo "[runner] No changes detected; skipping commit/push."
else
  git add -A
  git commit -m "AI work for ${ISSUE_KEY}: ${ISSUE_SUMMARY}"
  git push origin "${BRANCH_NAME}"

  # Create a draft PR if one does not already exist.
  # (Same logic as implemented in the repo's entrypoint.)
fi

echo "[runner] Completed successfully."
```

---

## 9. Guardrails and MCP Configuration

### 9.1 Rules (Guardrails)

**File:** `runner/.cursor/rules/ai-allowed.mdc`

```mdc
---
description: Cursor agent guardrails for autonomous development.
alwaysApply: true
---
# Constraints
- Only work on Jira issues labeled AI_ALLOWED and Ready for Dev.
- Branch name: ai/@{issueKey}-@{slug}
- Forbidden directories: infra/**, secrets/**
- Must add/modify tests.
- Must pass all tests before PR creation.

# Steps
1) Read ticket and acceptance criteria.
2) Plan and summarize changes in PR body.
3) Implement changes and run tests.
4) Open PR linking Jira ticket.
5) Respond to review feedback until approved.
```

---

### 9.2 MCP Configuration

**File:** `runner/.cursor/mcp.json`

```json
{
  "mcpServers": {
    "github": {
      "command": "npx",
      "args": ["-y", "mcp-github"],
      "env": { "GITHUB_TOKEN": "${GH_TOKEN}" }
    },
    "jira": {
      "command": "node",
      "args": ["./jira-mcp/dist/server.js"],
      "env": {
        "JIRA_BASE": "${JIRA_BASE}",
        "JIRA_EMAIL": "${JIRA_EMAIL}",
        "JIRA_API_TOKEN": "${JIRA_TOKEN}"
      }
    }
  }
}
```

---

## 10. Secrets and ConfigMaps

### 10.1 Secrets File

**File:** `k8s/secrets.env`

```env
GH_TOKEN=ghp_********************
REPO_URL=https://github.com/your-org/your-repo.git
JIRA_BASE=https://yourco.atlassian.net
JIRA_EMAIL=cursor-bot@yourco.com
JIRA_TOKEN=***************
OPENAI_API_KEY=sk-************************
AIDER_MODEL=openai/gpt-4o-mini
POST_RUN_COMMAND=
```

Create secret:

```bash
kubectl -n ai-dev create secret generic aider-secrets --from-env-file=k8s/secrets.env
```

---

### 10.2 ConfigMaps

```bash
kubectl -n ai-dev create configmap cursor-rules --from-file=runner/.cursor/rules/
kubectl -n ai-dev create configmap cursor-mcp --from-file=runner/.cursor/mcp.json
```

---

## 11. Local Testing Workflow

### 11.1 Cluster Setup

```bash
kind create cluster --name dev-ai
docker build -t cursor-runner:local ./runner
kind load docker-image cursor-runner:local --name dev-ai
kubectl apply -f k8s/namespace.yaml
kubectl apply -f k8s/rbac.yaml
```

### 11.2 Run Prefect Flow

```bash
prefect server start  # optional UI
python flows/cursor_flow.py
kubectl -n ai-dev logs -l job-name=cursor-runner-job -f
```

**Expected Results:**
- Pod runs to completion.
- Logs streamed in Prefect.
- Draft PR appears in the repository for the ai/{issueKey}-{slug} branch.

---

## 12. Acceptance Criteria

- ✅ Prefect successfully launches Kubernetes Job.
- ✅ Cursor CLI executes autonomously with guardrails.
- ✅ GitHub draft PR created or real PR opened.
- ✅ Logs captured in Prefect UI.
- ✅ Kubernetes Job self-deletes after success.

---

## 13. Future Enhancements

- Add **Snitch** post-job policy scoring task for risk evaluation.
- Integrate **Mabl** or **Playwright** as automated acceptance checks.
- Transition from **Kind → EKS/GKE** for production rollout.
- Implement **Jira webhooks** to auto-trigger Prefect flow.
- Add metrics (Prometheus + Grafana) for observability.

---

## 14. Deliverables

| Deliverable | Path | Description |
|--------------|------|-------------|
| Prefect Flow | `flows/cursor_flow.py` | Orchestrator |
| Runner Dockerfile | `runner/Dockerfile` | Headless agent image |
| Entrypoint Script | `runner/entrypoint.sh` | Agent bootstrap logic |
| Rules | `runner/.cursor/rules/ai-allowed.mdc` | Guardrails |
| MCP Config | `runner/.cursor/mcp.json` | API integration |
| Secrets | `k8s/secrets.env` | Tokens and repo details |
| RBAC | `k8s/rbac.yaml` | Permissions for jobs |
| Namespace | `k8s/namespace.yaml` | Logical group for jobs |

---

## 15. Summary

This PRD defines a complete, **local-first autonomous development system** using:
- **Cursor CLI** for headless code generation,
- **Prefect** for orchestration,
- **Kubernetes (Kind)** for isolation and scalability.

Once proven locally, this architecture can seamlessly transition to cloud infrastructure, making Cursor a **self-driving software developer** within controlled, auditable, and secure boundaries.
