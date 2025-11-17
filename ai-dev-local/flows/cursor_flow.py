import uuid

from prefect import flow, task, get_run_logger
from prefect_kubernetes import KubernetesJob

NAMESPACE = "ai-dev"


@task
def select_issue() -> dict:
    log = get_run_logger()
    return {
        "issue_key": "PAY-123",
        "issue_summary": "Example payment issue",
    }


def _build_env_list(overrides: dict) -> list:
    return [{"name": key, "value": value} for key, value in overrides.items()]


@flow(name="aider-agent-local")
def run_aider_agent(
    runner_image: str = "cursor-runner:local",
):
    log = get_run_logger()

    meta = select_issue()

    env_overrides = {
        "ISSUE_KEY": meta.get("issue_key", ""),
        "ISSUE_SUMMARY": meta.get("issue_summary", ""),
    }

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

    job = KubernetesJob(
        v1_job=job_manifest,
        delete_after_completion=True,
        interval_seconds=5,
        namespace=NAMESPACE,
    )

    log.info("Submitting Aider job...")
    job_run = job.trigger()
    job_run.wait_for_completion(print_func=log.info)
    logs = job_run.fetch_result()
    log.info(f"Job {job_name} completed. Captured logs from pods: {list(logs.keys())}")


if __name__ == "__main__":
    run_aider_agent()

