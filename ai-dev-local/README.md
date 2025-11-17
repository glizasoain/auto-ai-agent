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
  - `REPO_URL`: Target repository URL (e.g., `https://github.com/your-org/your-repo.git`)
  - `JIRA_BASE`: Jira instance base URL (e.g., `https://yourco.atlassian.net`)
  - `JIRA_EMAIL`: Bot account email
  - `JIRA_TOKEN`: Jira API token
  - `OPENAI_API_KEY`: API key with access to the desired OpenAI model (used by Aider)
  - `AIDER_MODEL` (optional): Override the default model (defaults to `openai/gpt-4o-mini`). Run `aider --list-models openai/` to discover what your key can access.
  - `POST_RUN_COMMAND` (optional): Shell command to execute after Aider succeeds (e.g., `npm test` or `pytest`)

> **Tip:** Aider requires an initialized git repository. The runner automatically creates one if missing and ignores `.aider*` artifacts.

### 4. Set Up Kubernetes Cluster

Create a local Kind cluster:

```bash
kind create cluster --name dev-ai #done
```

### 5. Build and Load Docker Image

Build the Cursor runner image and load it into Kind:

```bash
docker build -t cursor-runner:local ./runner #done
kind load docker-image cursor-runner:local --name dev-ai #done
```

### 6. Apply Kubernetes Resources

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

### Start Prefect Server (Optional)

For UI monitoring:

```bash
prefect server start
```

The Prefect UI will be available at `http://127.0.0.1:4200`

### Run the Flow

Execute the Prefect flow:

```bash
# Make sure your virtual environment is activated
source venv/bin/activate  # On Windows: venv\Scripts\activate

# Run the flow
python flows/cursor_flow.py
```

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

## Expected Results

- ✅ Prefect successfully launches the Aider runner Kubernetes Job
- ✅ Aider executes autonomously with guardrails defined in `.cursor/rules`
- ✅ GitHub draft PR created for every successful run
- ✅ Logs captured in Prefect UI and via `kubectl logs`
- ✅ Kubernetes Job self-deletes after success

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
├── flows/
│   └── cursor_flow.py           # Prefect orchestration flow
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

