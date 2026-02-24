# AGENTS.md

## Cursor Cloud specific instructions

### Product overview

This is a **Local Autonomous Coding Agent** that uses Aider (AI code assistant) orchestrated through Prefect workflows running inside Kubernetes (Kind) pods. It automatically picks up Jira issues, implements code changes, runs tests, and creates draft PRs on GitHub.

Two directories matter:
- `ai-dev-local/` — The main orchestration platform (Prefect flows, DB utilities, Kubernetes manifests, Docker runner, bootstrap tooling)
- `sample project/` — A trivial Flask "circle-demo" app used as a test target. Its `circle_demo` module is not implemented yet (it's the AI agent's job to write it), so sample project tests will fail and the Flask app won't start. This is expected.

### Services

| Service | How to start | Default port |
|---------|-------------|-------------|
| **MySQL** | `sudo mysqld --user=mysql --datadir=/var/lib/mysql &` | 3306 |
| **Prefect Server** | `source ai-dev-local/venv/bin/activate && prefect server start --host 127.0.0.1 &` | 4200 |
| **MinIO** | `docker run -d --name ai-dev-minio -e MINIO_ROOT_USER=aideradmin -e MINIO_ROOT_PASSWORD=aidersecret -p 9000:9000 -p 9001:9001 quay.io/minio/minio:latest server /data --console-address ":9001"` | 9000 (API), 9001 (Console) |

### Key gotchas

- **Docker in this VM**: Docker runs via `sudo dockerd &` since there's no systemd. After starting, run `sudo chmod 666 /var/run/docker.sock` for non-root access.
- **iptables**: Must use legacy mode (`sudo update-alternatives --set iptables /usr/sbin/iptables-legacy`) and fuse-overlayfs storage driver for Docker.
- **MinIO image**: The pinned MinIO image tag in `minio/docker-compose.yaml` (`RELEASE.2024-11-13T20-04-41Z`) may not be available. Use `quay.io/minio/minio:latest` or run the container directly instead of `docker compose`.
- **`flows/` has no `__init__.py`**: The flows directory is not a proper Python package. Run flow scripts from `ai-dev-local/` as CWD so Python resolves `from flows import ...` correctly.
- **secrets.env**: Must be created from `k8s/secrets.env.example` before running flows. The bootstrap script and flows auto-load it.
- **MySQL socket**: MySQL uses `/var/run/mysqld/mysqld.sock`. Ensure the directory exists and is owned by `mysql:mysql` before starting.

### Lint and test commands

- **Lint (flows)**: `cd ai-dev-local && source venv/bin/activate && ruff check flows/`
- **Lint (bootstrap)**: `ruff check tools/bootstrap.py` (has one pre-existing unused import warning)
- **Verify DB + flows**: `cd ai-dev-local && source venv/bin/activate && python3 -c "from flows import db; db.initialize_schema(log=None)"`
- **Sample project tests**: `cd "sample project" && source venv/bin/activate && pytest tests/ -v` (will fail until `circle_demo` module is implemented)

### Environment variables for flows

Set these before running flows (or they'll be auto-loaded from `k8s/secrets.env`):

```
MYSQL_HOST=127.0.0.1  MYSQL_PORT=3306  MYSQL_USER=cursor  MYSQL_PASSWORD=cursor-secret  MYSQL_DB=agent
PREFECT_API_URL=http://127.0.0.1:4200/api
```

External APIs (Jira, GitHub, OpenAI) require real credentials in `k8s/secrets.env` for end-to-end flow execution.
