#!/usr/bin/env python3
"""
One-stop bootstrapper for the autonomous Aider stack.

Usage:
    python tools/bootstrap.py up
"""

from __future__ import annotations

import argparse
import os
import shlex
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Dict

REPO_ROOT = Path(__file__).resolve().parents[1]
AI_DEV = REPO_ROOT / "ai-dev-local"
K8S_DIR = AI_DEV / "k8s"
SECRETS_FILE = K8S_DIR / "secrets.env"
VENV_DIR = AI_DEV / "venv"
LOG_DIR = AI_DEV / ".bootstrap"


def log(msg: str) -> None:
    print(f"[bootstrap] {msg}")


def run_cmd(
    cmd: list[str], *, cwd: Path | None = None, env: Dict[str, str] | None = None, check: bool = True
) -> subprocess.CompletedProcess:
    display = " ".join(shlex.quote(str(part)) for part in cmd)
    log(f"$ {display}")
    return subprocess.run(cmd, cwd=cwd, env=env, check=check)


def ensure_venv() -> Path:
    python_bin = VENV_DIR / "bin" / "python"
    pip_bin = VENV_DIR / "bin" / "pip"

    if not python_bin.exists():
        log("Creating virtual environment...")
        run_cmd(["python3", "-m", "venv", str(VENV_DIR)])

    log("Upgrading pip and installing requirements...")
    run_cmd([str(pip_bin), "install", "--upgrade", "pip"])
    run_cmd([str(pip_bin), "install", "-r", str(AI_DEV / "requirements.txt")])
    return python_bin


def load_env_file() -> Dict[str, str]:
    if not SECRETS_FILE.exists():
        raise FileNotFoundError(
            f"Missing secrets file at {SECRETS_FILE}. Run `cp k8s/secrets.env.example k8s/secrets.env` first."
        )
    env: Dict[str, str] = {}
    for line in SECRETS_FILE.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        env[key.strip()] = value.strip()
    return env


def _prefect_health_url(api_url: str) -> str:
    trimmed = api_url.rstrip("/")
    if trimmed.endswith("/api"):
        trimmed = trimmed[:-4]
    return f"{trimmed.rstrip('/')}/api/health"


def prefect_server_ready(api_url: str, timeout: float = 2.0) -> bool:
    url = _prefect_health_url(api_url)
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            return resp.status == 200
    except (urllib.error.URLError, TimeoutError):
        return False


def start_prefect_server(prefect_bin: Path, env: Dict[str, str]) -> None:
    LOG_DIR.mkdir(exist_ok=True)
    log_file = LOG_DIR / "prefect-server.log"
    pid_file = LOG_DIR / "prefect-server.pid"
    log("Starting Prefect server in the background...")
    with open(log_file, "ab") as stream:
        proc = subprocess.Popen(
            [str(prefect_bin), "server", "start", "--host", "127.0.0.1"],
            cwd=AI_DEV,
            env=env,
            stdout=stream,
            stderr=stream,
        )
    pid_file.write_text(str(proc.pid))


def ensure_prefect_server(prefect_bin: Path, env: Dict[str, str]) -> None:
    api_url = env.setdefault("PREFECT_API_URL", "http://127.0.0.1:4200/api")
    if prefect_server_ready(api_url):
        log(f"Prefect server already reachable at {api_url}")
        return
    start_prefect_server(prefect_bin, env)
    log("Waiting for Prefect server to become ready...")
    deadline = time.time() + 120
    while time.time() < deadline:
        if prefect_server_ready(api_url):
            log("Prefect server is online.")
            return
        time.sleep(2)
    raise RuntimeError("Prefect server failed to start within the timeout window.")


def ensure_minio_bucket(env: Dict[str, str]) -> None:
    bucket = env.get("AIDER_SESSION_BUCKET")
    if not bucket:
        log("Skipping bucket creation (AIDER_SESSION_BUCKET not set).")
        return
    aws_cli = shutil.which("aws")
    if not aws_cli:
        log("Skipping bucket verification (aws CLI not installed).")
        return
    endpoint = env.get("S3_ENDPOINT_URL")
    args = [aws_cli, "s3api", "head-bucket", "--bucket", bucket]
    if endpoint:
        args += ["--endpoint-url", endpoint]
    result = subprocess.run(args, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if result.returncode == 0:
        log(f"S3 bucket {bucket} already exists.")
        return
    log(f"Creating bucket {bucket}...")
    create_cmd = [aws_cli, "s3api", "create-bucket", "--bucket", bucket]
    if endpoint:
        create_cmd += ["--endpoint-url", endpoint]
    subprocess.run(create_cmd, env=env, check=True)


def apply_k8s_resources() -> None:
    manifests = [
        K8S_DIR / "namespace.yaml",
        K8S_DIR / "rbac.yaml",
        K8S_DIR / "configmap-rules.yaml",
        K8S_DIR / "configmap-mcp.yaml",
    ]
    for manifest in manifests:
        run_cmd(["kubectl", "apply", "-f", str(manifest)])
    run_cmd(["kubectl", "-n", "ai-dev", "delete", "secret", "aider-secrets", "--ignore-not-found=true"])
    run_cmd(
        [
            "kubectl",
            "-n",
            "ai-dev",
            "create",
            "secret",
            "generic",
            "aider-secrets",
            f"--from-env-file={SECRETS_FILE}",
        ]
    )


def ensure_mysql_schema(python_bin: Path, env: Dict[str, str]) -> None:
    log("Ensuring MySQL schema exists...")
    run_cmd(
        [
            str(python_bin),
            "-c",
            "from flows import db; db.initialize_schema(log=None)",
        ],
        cwd=AI_DEV,
        env=env,
    )


def ensure_work_pool(prefect_bin: Path, env: Dict[str, str]) -> str:
    pool = env.get("PREFECT_WORK_POOL") or env.get("PREFECT_DEFAULT_WORK_POOL_NAME") or "default-agent-pool"
    result = subprocess.run(
        [str(prefect_bin), "work-pool", "inspect", pool],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if result.returncode == 0:
        log(f"Prefect work pool '{pool}' already exists.")
        return pool
    log(f"Creating Prefect work pool '{pool}'...")
    run_cmd([str(prefect_bin), "work-pool", "create", pool, "--type", "process"], env=env)
    return pool


def register_deployments(python_bin: Path, env: Dict[str, str]) -> None:
    log("Building Prefect deployments...")
    run_cmd([str(python_bin), "-m", "flows.deployments"], cwd=AI_DEV, env=env)


def cmd_up() -> None:
    python_bin = ensure_venv()
    prefect_bin = VENV_DIR / "bin" / "prefect"
    base_env = os.environ.copy()
    file_env = load_env_file()
    base_env.update(file_env)
    base_env["PATH"] = f"{VENV_DIR / 'bin'}:{base_env.get('PATH', '')}"

    ensure_prefect_server(prefect_bin, base_env)
    ensure_minio_bucket(base_env)
    apply_k8s_resources()
    ensure_mysql_schema(python_bin, base_env)
    ensure_work_pool(prefect_bin, base_env)
    register_deployments(python_bin, base_env)
    log("Bootstrap complete. You can now start your Prefect worker:")
    log(f"  source ai-dev-local/venv/bin/activate && PREFECT_API_URL={base_env.get('PREFECT_API_URL','http://127.0.0.1:4200/api')} prefect worker start --pool <your-pool>")


def main() -> None:
    parser = argparse.ArgumentParser(description="Bootstrap local autonomous agent stack.")
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("up", help="Install dependencies, start Prefect server, sync secrets, and register deployments.")
    args = parser.parse_args()
    if args.command == "up":
        cmd_up()
    else:
        parser.print_help()


if __name__ == "__main__":
    main()

