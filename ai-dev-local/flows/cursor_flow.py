import json
import os
import re
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Set

import requests
from prefect import flow, get_run_logger, task
from prefect_kubernetes import KubernetesJob

from flows import db as db_utils

NAMESPACE = "ai-dev"
DEFAULT_JQL = 'status = "Ready for Development" AND labels = AI_ALLOWED ORDER BY updated DESC'
REPO_URL_PATTERN = re.compile(
    r"https://github\.com/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+(?:\.git)?",
    re.IGNORECASE,
)
DEFAULT_SECRETS_PATH = Path(__file__).resolve().parent.parent / "k8s" / "secrets.env"
RUNNER_RESULT_PREFIX = "::runner-result::"
REQUIRED_SECRET_KEYS = (
    "JIRA_BASE",
    "JIRA_EMAIL",
    "JIRA_TOKEN",
    "GH_TOKEN",
    "MYSQL_USER",
    "MYSQL_PASSWORD",
    "MYSQL_DB",
)


def _build_env_list(overrides: dict) -> list:
    return [{"name": key, "value": value} for key, value in overrides.items()]


def _slugify(text: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return slug[:40] or "update"


def _build_branch_name(issue_key: str, issue_summary: str) -> str:
    summary_slug = _slugify(issue_summary or issue_key)
    run_token = uuid.uuid4().hex[:6]
    return f"ai/{issue_key}/{summary_slug}-{run_token}"


def _repo_slug(repo_url: str) -> str:
    cleaned = repo_url.strip().rstrip("/")
    if cleaned.endswith(".git"):
        cleaned = cleaned[:-4]
    return cleaned.split("https://github.com/")[-1]


def _github_headers(token: str) -> Dict[str, str]:
    return {
        "Authorization": f"token {token}",
        "Accept": "application/vnd.github+json",
    }


def _adf_node_to_text(node: Any) -> str:
    """Best-effort conversion of Jira's Atlassian Document Format to plain text."""
    if node is None:
        return ""
    if isinstance(node, str):
        return node
    if isinstance(node, list):
        return "\n".join(filter(None, (_adf_node_to_text(item) for item in node)))
    if isinstance(node, dict):
        node_type = node.get("type")
        if node_type == "inlineCard":
            url = node.get("attrs", {}).get("url")
            return url or ""
        if node_type == "text":
            return node.get("text", "")
        return "\n".join(filter(None, (_adf_node_to_text(child) for child in node.get("content", []))))
    return ""


def _extract_repo_url(description_text: str) -> Optional[str]:
    if not description_text:
        return None
    match = REPO_URL_PATTERN.search(description_text)
    if not match:
        return None
    url = match.group(0)
    return url[:-4] if url.endswith(".git") else url


def _fetch_jira_issue(skip_issue_keys: Optional[Sequence[str]] = None) -> Dict[str, str]:
    base = os.getenv("JIRA_BASE")
    email = os.getenv("JIRA_EMAIL")
    token = os.getenv("JIRA_TOKEN")
    if not all([base, email, token]):
        raise RuntimeError("Missing JIRA_BASE, JIRA_EMAIL, or JIRA_TOKEN environment variables.")

    jql = os.getenv("JIRA_JQL", DEFAULT_JQL)
    max_results = int(os.getenv("JIRA_MAX_RESULTS", "5"))
    request_url = f"{base.rstrip('/')}/rest/api/3/search/jql"
    skip_keys: Set[str] = set(skip_issue_keys or [])

    response = requests.post(
        request_url,
        params={"maxResults": max_results},
        json={
            "jql": jql,
            "fields": ["summary", "description"],
            "fieldsByKeys": False,
        },
        auth=(email, token),
        headers={"Accept": "application/json"},
        timeout=30,
    )
    if response.status_code >= 400:
        raise RuntimeError(f"Jira search failed ({response.status_code}): {response.text}")

    data = response.json()
    issues: List[Dict[str, Any]] = data.get("issues", [])
    if not issues:
        raise RuntimeError("No Jira issues matched the AI_ALLOWED / Ready for Development criteria.")

    fallback_repo = os.getenv("REPO_URL", "").strip()

    for issue in issues:
        if issue["key"] in skip_keys:
            continue
        fields = issue.get("fields", {})
        summary = (fields.get("summary") or "").strip()
        description = fields.get("description")
        description_text = _adf_node_to_text(description)
        repo_url = _extract_repo_url(description_text)
        if repo_url:
            return {
                "issue_key": issue["key"],
                "issue_summary": summary or issue["key"],
                "repo_url": repo_url,
            }

    if fallback_repo:
        for issue in issues:
            if issue["key"] in skip_keys:
                continue
            return {
                "issue_key": issue["key"],
                "issue_summary": (issue.get("fields", {}).get("summary") or issue["key"]),
                "repo_url": fallback_repo,
            }

    raise RuntimeError(
        "Found Jira issues, but none contained a GitHub repository URL in the description. "
        "Add a line like 'Repo: https://github.com/org/repo' to the ticket body, or set REPO_URL "
        "in k8s/secrets.env as a fallback."
    )


def _load_local_secrets_if_needed(log) -> None:
    """Allow local Prefect runs to read k8s/secrets.env automatically."""
    secrets_path = Path(os.getenv("SECRETS_ENV_PATH", DEFAULT_SECRETS_PATH))
    missing = [key for key in REQUIRED_SECRET_KEYS if not os.getenv(key)]
    if not missing:
        return

    if not secrets_path.exists():
        log.warning("Missing env vars (%s) and secrets file %s.", ", ".join(missing), secrets_path)
        return

    log.info("Loading env vars from %s for local flow execution.", secrets_path)
    with secrets_path.open() as fh:
        for line in fh:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip()
            value = value.strip()
            if key and not os.getenv(key):
                os.environ[key] = value


def _parse_runner_result(logs: Dict[str, str]) -> Dict[str, Any]:
    if not logs:
        return {}
    for pod_logs in logs.values():
        for line in pod_logs.splitlines():
            if RUNNER_RESULT_PREFIX in line:
                payload = line.split(RUNNER_RESULT_PREFIX, 1)[1].strip()
                try:
                    return json.loads(payload)
                except json.JSONDecodeError:
                    continue
    return {}


def _lookup_pr_by_branch(repo_url: str, branch_name: str, token: str) -> Dict[str, Any]:
    if not branch_name:
        return {}
    slug = _repo_slug(repo_url)
    owner = slug.split("/")[0]
    response = requests.get(
        f"https://api.github.com/repos/{slug}/pulls",
        params={"head": f"{owner}:{branch_name}", "state": "all"},
        headers=_github_headers(token),
        timeout=30,
    )
    if response.status_code >= 400:
        return {}
    data = response.json()
    if not data:
        return {}
    pr = data[0]
    return {
        "pr_number": pr.get("number"),
        "pr_url": pr.get("html_url"),
        "branch_name": branch_name,
        "state": pr.get("state"),
        "merged": bool(pr.get("merged_at")),
        "head_sha": pr.get("head", {}).get("sha"),
    }


def _lookup_pr_by_number(repo_url: str, pr_number: int, token: str) -> Dict[str, Any]:
    slug = _repo_slug(repo_url)
    response = requests.get(
        f"https://api.github.com/repos/{slug}/pulls/{pr_number}",
        headers=_github_headers(token),
        timeout=30,
    )
    if response.status_code >= 400:
        return {}
    pr = response.json()
    return {
        "pr_number": pr.get("number"),
        "pr_url": pr.get("html_url"),
        "state": pr.get("state"),
        "merged": bool(pr.get("merged_at")),
        "branch_name": pr.get("head", {}).get("ref"),
        "head_sha": pr.get("head", {}).get("sha"),
    }


def _lookup_latest_review(repo_url: str, pr_number: int, token: str) -> Optional[Dict[str, Any]]:
    slug = _repo_slug(repo_url)
    response = requests.get(
        f"https://api.github.com/repos/{slug}/pulls/{pr_number}/reviews",
        headers=_github_headers(token),
        timeout=30,
    )
    if response.status_code >= 400:
        return None
    reviews = response.json()
    if not reviews:
        return None
    reviews.sort(key=lambda item: item.get("submitted_at") or "", reverse=True)
    return reviews[0]


def _collect_pr_context(meta: Dict[str, Any], logs: Dict[str, str], log) -> Dict[str, Any]:
    context = {}
    runner_result = _parse_runner_result(logs)
    if runner_result:
        context.update(runner_result)

    branch_name = meta.get("branch_name") or runner_result.get("branch_name")
    token = os.getenv("GH_TOKEN")
    if not context.get("pr_number") and token and branch_name:
        lookup = _lookup_pr_by_branch(meta["repo_url"], branch_name, token)
        if lookup:
            context.update(lookup)
    elif not token:
        log.warning("GH_TOKEN missing; unable to look up PR metadata.")
    return context


def _build_runner_env(meta: Dict[str, Any], base_branch: str) -> Dict[str, str]:
    env = {
        "ISSUE_KEY": meta.get("issue_key", ""),
        "ISSUE_SUMMARY": meta.get("issue_summary", ""),
        "REPO_URL": meta.get("repo_url", ""),
        "BASE_BRANCH": base_branch,
    }
    if meta.get("branch_name"):
        env["BRANCH_NAME"] = meta["branch_name"]
    if meta.get("pr_number"):
        env["PR_NUMBER"] = str(meta["pr_number"])
    return env


def _trigger_runner(meta: Dict[str, Any], log, runner_image: str) -> Dict[str, Any]:
    base_branch = os.getenv("BASE_BRANCH", "main")
    env_overrides = _build_runner_env(meta, base_branch)
    job_name = f"aider-runner-job-{uuid.uuid4().hex[:8]}"

    job_manifest = {
        "apiVersion": "batch/v1",
        "kind": "Job",
        "metadata": {"name": job_name, "namespace": NAMESPACE, "labels": {"app": "aider-runner"}},
        "spec": {
            "backoffLimit": 0,
            "template": {
                "metadata": {"labels": {"app": "aider-runner", "job-name": job_name}},
                "spec": {
                    "serviceAccountName": "job-launcher",
                    "restartPolicy": "Never",
                    "containers": [
                        {
                            "name": "runner",
                            "image": runner_image,
                            "imagePullPolicy": "IfNotPresent",
                            "env": _build_env_list(env_overrides),
                            "envFrom": [{"secretRef": {"name": "aider-secrets"}}],
                            "volumeMounts": [
                                {
                                    "name": "cursor-rules",
                                    "mountPath": "/workspace/.cursor/rules",
                                    "readOnly": True,
                                },
                                {
                                    "name": "cursor-mcp",
                                    "mountPath": "/workspace/.cursor/mcp.json",
                                    "subPath": "mcp.json",
                                    "readOnly": True,
                                },
                            ],
                        }
                    ],
                    "volumes": [
                        {
                            "name": "cursor-rules",
                            "configMap": {"name": "cursor-rules"},
                        },
                        {
                            "name": "cursor-mcp",
                            "configMap": {"name": "cursor-mcp"},
                        },
                    ],
                },
            },
        },
    }

    db_utils.update_run(
        meta["issue_key"],
        status=db_utils.STATUS_IN_PROGRESS,
        branch_name=meta.get("branch_name"),
        last_runner_id=job_name,
    )

    job = KubernetesJob(
        v1_job=job_manifest,
        delete_after_completion=True,
        interval_seconds=5,
        namespace=NAMESPACE,
    )

    log.info("Submitting Aider job %s for issue %s...", job_name, meta["issue_key"])
    try:
        job_run = job.trigger()
        job_run.wait_for_completion(print_func=log.info)
        logs = job_run.fetch_result()
        log.info("Job %s completed. Pods: %s", job_name, list(logs.keys()))
    except Exception as exc:  # noqa: BLE001
        db_utils.update_run(meta["issue_key"], status=db_utils.STATUS_FAILED)
        raise exc

    pr_context = _collect_pr_context(meta, logs, log)
    metadata = dict(meta.get("metadata") or {})
    if pr_context:
        metadata.update(
            {
                "pr_url": pr_context.get("pr_url"),
                "head_sha": pr_context.get("head_sha"),
                "last_runner_result": pr_context,
            }
        )
    metadata["last_runner_id"] = job_name

    update_fields: Dict[str, Any] = {
        "status": db_utils.STATUS_IN_REVIEW if pr_context.get("pr_number") else db_utils.STATUS_IN_PROGRESS,
        "branch_name": meta.get("branch_name"),
        "metadata": metadata,
        "last_runner_id": job_name,
    }
    if pr_context.get("pr_number"):
        update_fields["pr_number"] = pr_context["pr_number"]

    db_utils.update_run(meta["issue_key"], **update_fields)
    return pr_context


@task
def select_issue() -> dict:
    log = get_run_logger()
    _load_local_secrets_if_needed(log)
    db_utils.initialize_schema(log)

    skip_keys = db_utils.get_active_issue_keys()
    attempts = 0

    while True:
        issue = _fetch_jira_issue(skip_keys)
        branch_name = _build_branch_name(issue["issue_key"], issue["issue_summary"])
        record = db_utils.claim_issue(
            issue_key=issue["issue_key"],
            issue_summary=issue["issue_summary"],
            repo_url=issue["repo_url"],
            branch_name=branch_name,
        )
        if record:
            issue["branch_name"] = record.get("branch_name") or branch_name
            log.info(
                "Selected Jira issue %s targeting repo %s on branch %s",
                issue["issue_key"],
                issue["repo_url"],
                issue["branch_name"],
            )
            return issue

        skip_keys.add(issue["issue_key"])
        attempts += 1
        if attempts >= int(os.getenv("ISSUE_SELECTION_MAX_ATTEMPTS", "5")):
            raise RuntimeError("Unable to claim a unique Jira issue; try again shortly.")


@flow(name="aider-agent-local")
def run_aider_agent(runner_image: str = "cursor-runner:local"):
    log = get_run_logger()
    meta = select_issue()
    _trigger_runner(meta, log, runner_image)


@flow(name="aider-pr-monitor")
def monitor_pull_requests(runner_image: str = "cursor-runner:local", monitor_limit: int = 10):
    log = get_run_logger()
    _load_local_secrets_if_needed(log)
    db_utils.initialize_schema(log)

    token = os.getenv("GH_TOKEN")
    if not token:
        log.warning("GH_TOKEN not set; skipping PR monitoring.")
        return

    runs = db_utils.get_runs_by_status(
        [db_utils.STATUS_IN_REVIEW, db_utils.STATUS_CHANGES_REQUESTED],
        limit=monitor_limit,
    )
    if not runs:
        log.info("No PRs waiting for review.")
        return

    for run in runs:
        pr_number = run.get("pr_number")
        if not pr_number:
            log.warning("Run %s has no PR number yet; skipping.", run["issue_key"])
            continue

        pr_info = _lookup_pr_by_number(run["repo_url"], pr_number, token)
        if not pr_info:
            log.warning("Unable to load PR %s for %s.", pr_number, run["issue_key"])
            continue

        metadata = dict(run.get("metadata") or {})
        metadata["last_pr_poll"] = pr_info

        if pr_info["merged"]:
            db_utils.update_run(run["issue_key"], status=db_utils.STATUS_MERGED, metadata=metadata)
            log.info("Issue %s merged; marking as complete.", run["issue_key"])
            continue

        if pr_info["state"] == "closed":
            db_utils.update_run(run["issue_key"], status=db_utils.STATUS_FAILED, metadata=metadata)
            log.info("Issue %s PR closed without merge.", run["issue_key"])
            continue

        latest_review = _lookup_latest_review(run["repo_url"], pr_number, token)
        if latest_review and latest_review.get("state") == "CHANGES_REQUESTED":
            last_seen_review = metadata.get("last_review_id")
            if last_seen_review != latest_review["id"]:
                metadata["last_review_id"] = latest_review["id"]
                metadata["last_review_state"] = latest_review.get("state")
                db_utils.update_run(run["issue_key"], status=db_utils.STATUS_CHANGES_REQUESTED, metadata=metadata)
                log.info("Reviewer requested changes for %s; launching follow-up run.", run["issue_key"])
                rerun_meta = {
                    "issue_key": run["issue_key"],
                    "issue_summary": run["issue_summary"],
                    "repo_url": run["repo_url"],
                    "branch_name": run["branch_name"],
                    "pr_number": run["pr_number"],
                    "metadata": metadata,
                }
                _trigger_runner(rerun_meta, log, runner_image)
            else:
                log.info("Awaiting updated review for %s; no new feedback.", run["issue_key"])
        else:
            metadata["last_review_state"] = latest_review.get("state") if latest_review else "PENDING"
            db_utils.update_run(run["issue_key"], metadata=metadata)


if __name__ == "__main__":
    run_aider_agent()

