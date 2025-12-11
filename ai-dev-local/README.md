# Autonomous Aider Agent - Local Setup

A local autonomous coding agent system that orchestrates an Aider-powered developer inside Kubernetes pods via Prefect to automatically handle Jira/GitHub issues, implement code, run tests, and prepare PRs.

## Prerequisites

Install the following tools on your system:

```bash
# macOS
brew install kind kubectl node python git #done

# Linux (Ubuntu/Debian)
sudo apt-get update
sudo apt-get install -y kubectl nodejs npm python3 python3-pip git
# Install kind from: https://kind.sigs.k8s.io/docs/user/quick-start/#installation
```

## Setup

### 1. Create Virtual Environment

```bash
cd ai-dev-local #done
python3 -m venv venv #done
source venv/bin/activate  #done # On Windows: venv\Scripts\activate
```

### 2. Install Python Dependencies

```bash
pip install -r requirements.txt #done
```

### 3. Configure Secrets #done  

Copy the example secrets file and fill in your credentials:

```bash
cp k8s/secrets.env.example k8s/secrets.env
# Edit k8s/secrets.env with your actual tokens and repository URL
```

- **Required variables:**
  - `GH_TOKEN`: GitHub personal access token with repo and PR permissions
  - `JIRA_BASE`: Jira instance base URL (e.g., `https://yourco.atlassian.net`)
  - `JIRA_EMAIL`: Bot account email
  - `JIRA_TOKEN`: Jira API token
  - `OPENAI_API_KEY`: API key with access to the desired OpenAI model (used by Aider)
  - `MYSQL_USER`, `MYSQL_PASSWORD`, `MYSQL_DB`: credentials for the MySQL state store (defaults for `MYSQL_HOST`/`MYSQL_PORT` are `127.0.0.1:3306`, but you can override them as needed)
  - `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`: credentials for the S3/MinIO bucket storing Aider transcripts
  - `S3_ENDPOINT_URL`: S3-compatible endpoint (e.g., `http://127.0.0.1:9000` when using MinIO)
  - `AIDER_SESSION_BUCKET`: bucket name (e.g., `aider-sessions`)
  - `AIDER_SESSION_PREFIX`: key prefix within the bucket (e.g., `sessions/`)
- **Optional overrides:**
  - `REPO_URL`: Fallback repository URL if the Jira ticket does not specify one (the flow now prefers URLs embedded in the Jira description)
  - `AIDER_MODEL` (optional): Override the default model (defaults to `openai/gpt-4o-mini`). Run `aider --list-models openai/` to discover what your key can access.
  - `POST_RUN_COMMAND` (optional): Shell command to execute after Aider succeeds (e.g., `npm test` or `pytest`)
  - `AWS_REGION`: Defaults to `us-east-1` if omitted

> **Tip:** Aider requires an initialized git repository. The runner automatically creates one if missing and ignores `.aider*` artifacts.

### 4. Provision MySQL State Store

The agent now persists issue → branch → PR state in MySQL so it can resume work after review feedback and avoid double-claiming Jira tickets.

1. Install MySQL locally (`brew install mysql` on macOS).
2. Start the server (`brew services start mysql`) or use an existing instance reachable from your Prefect environment.
3. Create the database/user:

```sql
CREATE DATABASE agent CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
CREATE USER 'cursor'@'%' IDENTIFIED BY 'cursor-secret';
GRANT ALL PRIVILEGES ON agent.* TO 'cursor'@'%';
FLUSH PRIVILEGES;
```

Update `k8s/secrets.env` with the matching `MYSQL_*` variables. Only `MYSQL_USER`, `MYSQL_PASSWORD`, and `MYSQL_DB` are strictly required—the flow defaults to `MYSQL_HOST=127.0.0.1` and `MYSQL_PORT=3306` if not provided.

### 5. Set Up Kubernetes Cluster

Create a local Kind cluster:

```bash
kind create cluster --name dev-ai #done
```

### 6. Build and Load Docker Image

Build the Cursor runner image and load it into Kind:

```bash
docker build -t cursor-runner:local ./runner #done
kind load docker-image cursor-runner:local --name dev-ai #done
```

### 7. Start MinIO (S3-Compatible Session Store)

The runner persists its entire Aider transcript to an S3 bucket so reviewer feedback loops keep full context. Locally we run MinIO:

```bash
cd ai-dev-local
docker compose -f minio/docker-compose.yaml up -d
```

Open http://127.0.0.1:9001 to access the MinIO console (default credentials are defined in `minio/docker-compose.yaml`). Create the bucket that stores chat history (you can rename it, but keep `k8s/secrets.env` in sync):

```bash
export AWS_ACCESS_KEY_ID=aideradmin
export AWS_SECRET_ACCESS_KEY=aidersecret
aws --endpoint-url http://127.0.0.1:9000 s3 mb s3://aider-sessions
```

### 8. Apply Kubernetes Resources

Create the namespace, RBAC, secrets, and ConfigMaps:

```bash
# Apply namespace and RBAC
kubectl apply -f k8s/namespace.yaml #done
kubectl apply -f k8s/rbac.yaml #done

# Create secrets from environment file
kubectl -n ai-dev create secret generic aider-secrets --from-env-file=k8s/secrets.env #done

# Apply ConfigMaps
kubectl apply -f k8s/configmap-rules.yaml #done
kubectl apply -f k8s/configmap-mcp.yaml #done
```

## Usage

### Bootstrap Everything (Recommended)

The repo now ships with a Python CLI that prepares the entire environment (venv, Prefect server, Kubernetes resources, secrets, MySQL schema, MinIO bucket, and Prefect deployments):

```bash
python tools/bootstrap.py up
```

The command is idempotent and safe to re-run whenever credentials change. It will also verify that your configured `AIDER_SESSION_BUCKET` exists (creating it on MinIO when possible).

### Start Prefect Server (Optional)

For UI monitoring:

```bash
prefect server start
```

The Prefect UI will be available at `http://127.0.0.1:4200`

### Run the Flow

You can still execute flows manually (useful for debugging a single run):

```bash
# Make sure your virtual environment is activated
source venv/bin/activate  # On Windows: venv\Scripts\activate

# Run the flow
python flows/cursor_flow.py
```

### Continuous Autonomous Mode

To keep the agent always-on, register Prefect deployments for both the Jira intake flow (`run_aider_agent`) and the PR monitor (`monitor_pull_requests`):

```bash
# With your virtualenv active
python -m flows.deployments

# Start an agent that will execute both schedules (defaults shown in flows/deployments.py)
prefect agent start -q aider-agent
```

- `run_aider_agent` runs on a cron (default every 10 minutes) to claim fresh Jira issues and launch runner pods.
- `monitor_pull_requests` runs more frequently (default every 5 minutes) to poll GitHub PRs, detect review feedback, and trigger follow-up runs on the same branch when reviewers request changes.

You can also run the monitor ad-hoc:

```bash
python - <<'PY'
from flows.cursor_flow import monitor_pull_requests
monitor_pull_requests()
PY
```

### Jira Issue Selection

- The `select_issue` task queries Jira for tickets labeled `AI_ALLOWED` and with status “Ready for Development.”
- Set `JIRA_BASE`, `JIRA_EMAIL`, and `JIRA_TOKEN` in `k8s/secrets.env`. Optional overrides:
  - `JIRA_JQL` – custom JQL if you want to narrow or widen the search.
  - `JIRA_MAX_RESULTS` – how many matching issues to inspect (default 5).
- Claimed issues are persisted in the MySQL `agent_runs` table so Prefect will not double-claim a ticket while it is in progress or under review.
- For each issue the flow inspects, it looks for the first GitHub repository URL inside the ticket description (add something like `Repo: https://github.com/your-org/your-repo`). That URL becomes the `REPO_URL` passed to the runner, so the pod clones the correct project automatically.
- If no repo URL is present, the flow falls back to the `REPO_URL` value in `k8s/secrets.env` (if set). Otherwise it fails fast so you can update the Jira ticket description.
- The selected issue key/summary, repo URL, and base branch (defaults to `main` unless `BASE_BRANCH` is set) are injected into the runner as environment variables before the Kubernetes job starts.
- When you run the Prefect flow locally, it automatically loads `k8s/secrets.env` (or the file defined in `SECRETS_ENV_PATH`) so you don’t need to `source` the env file manually; Kubernetes pods still get their values from the `aider-secrets` secret.

### Monitor Job Execution

Watch the logs in real-time:

```bash
kubectl -n ai-dev logs -l job-name=cursor-runner-job -f
```

Or check job status:

```bash
kubectl -n ai-dev get jobs
kubectl -n ai-dev get pods
```

### State Tracking & Review Handling

- Every Jira issue claimed by the flow gets a row in `agent_runs` (see `db/schema.sql`). The row captures repo URL, branch name, PR number, current status, and metadata about the latest runner execution.
- The Kubernetes runner now emits a machine-readable `::runner-result::` log line with branch/PR info, allowing the Prefect flow to immediately update the database after each job.
- The `monitor_pull_requests` flow polls GitHub for runs in `in_review` or `changes_requested` states:
  - Merged PRs are marked complete (`status=merged`).
  - Closed/unmerged PRs are flagged as `failed`.
  - New reviewer requests automatically trigger another runner pod that reuses the same branch and PR so context is preserved.
- Metadata tracks the last processed review ID to prevent duplicate reruns for the same review comment set.

## Expected Results

- ✅ Prefect successfully launches the Aider runner Kubernetes Job
- ✅ Aider executes autonomously with guardrails defined in `.cursor/rules`
- ✅ GitHub draft PR created for every successful run
- ✅ Logs captured in Prefect UI and via `kubectl logs`
- ✅ Kubernetes Job self-deletes after success
- ✅ MySQL state store tracks claimed Jira issues, branches, and PR review status for autonomous follow-ups

## Directory Structure

```
ai-dev-local/
├── k8s/
│   ├── namespace.yaml          # Kubernetes namespace
│   ├── rbac.yaml               # ServiceAccount, Role, RoleBinding
│   ├── secrets.env.example     # Template for secrets
│   ├── configmap-rules.yaml    # Cursor guardrails
│   └── configmap-mcp.yaml      # MCP server configuration
├── runner/
│   ├── Dockerfile              # Container image definition
│   ├── entrypoint.sh           # Agent bootstrap script
│   └── .cursor/
│       ├── rules/
│       │   └── ai-allowed.mdc  # Guardrails and constraints
│       └── mcp.json            # MCP server config
├── db/
│   └── schema.sql              # MySQL schema for agent state
├── flows/
│   ├── cursor_flow.py          # Prefect orchestration flows
│   ├── db.py                   # MySQL helper utilities
│   └── deployments.py          # Prefect deployment builder
├── requirements.txt            # Python dependencies
├── .gitignore                 # Git ignore rules
└── README.md                  # This file
```

## Aider Integration

Cursor currently lacks an official CLI, so this project uses **[Aider](https://github.com/Aider-AI/aider)** as the autonomous coding agent:

- The runner container installs `aider-chat`
- `OPENAI_API_KEY` (and optional `AIDER_MODEL`) are injected via Kubernetes secrets
- Prefect injects `ISSUE_KEY` and `ISSUE_SUMMARY` env vars for real work items
- `runner/entrypoint.sh` clones the repo, injects the issue metadata, and launches `aider`
- Branch naming convention: `ai/<ISSUE_KEY>/<summary-slug>-<run-id>`, where the slug is derived from the Jira summary (max 40 chars) and `run-id` is a UTC timestamp, ensuring each run pushes a unique branch while keeping the ticket context obvious.
- Reruns can reuse an existing `BRANCH_NAME`/`PR_NUMBER`, and the runner emits a `::runner-result::` JSON log so Prefect can persist branch/PR metadata immediately after each pod finishes.
- Guardrails (`.cursor/rules/ai-allowed.mdc`) remain in place; the prompt reminds Aider to follow them
- Optional post-Aider steps (tests, linting) can be customized in the entrypoint script

You can customize behavior by editing `runner/entrypoint.sh` (prompt text, additional commands) or by passing extra flags through environment variables. To inspect available OpenAI models for your key, run:

```bash
source venv/bin/activate
aider --list-models openai/
```

If you install Aider locally via `aider-install`, ensure `~/.local/bin` is on your `PATH` so the `aider` binary is accessible.

> **Model availability:** This list may include future-dated models. Your OpenAI account must be entitled to a given model for Aider to use it successfully.

## Validation & Troubleshooting

### Validate Aider Access

```bash
source venv/bin/activate
aider --list-models openai/          # confirm your key can see required models
echo $OPENAI_API_KEY | head -c 5     # sanity check key is loaded (do not print entire key)
```

If `aider` is not found, add `export PATH="$HOME/.local/bin:$PATH"` (or run `uv tool update-shell`) in your shell profile.

### Rebuild and Reload Runner

```bash
docker build -t cursor-runner:local ./runner
kind load docker-image cursor-runner:local --name dev-ai
```

### Reapply Secrets After Changes

```bash
kubectl -n ai-dev delete secret aider-secrets --ignore-not-found
kubectl -n ai-dev create secret generic aider-secrets --from-env-file=k8s/secrets.env
```

## Troubleshooting

### Job Fails to Start

Check pod events:
```bash
kubectl -n ai-dev describe pod <pod-name>
```

### Image Pull Errors

Ensure the image is loaded into Kind:
```bash
kind load docker-image cursor-runner:local --name dev-ai
```

### Secret Issues

Verify secrets are created:
```bash
kubectl -n ai-dev get secret aider-secrets
```

### Prefect Connection Issues

Ensure Prefect server is running or configure Prefect Cloud:
```bash
prefect config view
```

## Cleanup

Remove the Kind cluster:

```bash
kind delete cluster --name dev-ai
```

## Future Enhancements

- Add Snitch post-job policy scoring for risk evaluation
- Integrate Mabl or Playwright for automated acceptance checks
- Transition from Kind → EKS/GKE for production rollout
- Implement Jira webhooks to auto-trigger Prefect flow
- Add metrics (Prometheus + Grafana) for observability

## License

See LICENSE file for details.

