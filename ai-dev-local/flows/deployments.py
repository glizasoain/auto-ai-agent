"""
Prefect deployment helpers for the autonomous Cursor agent.

Usage:
    python -m flows.deployments
"""

import os
from pathlib import Path

from prefect.client.schemas.schedules import CronSchedule

from flows.cursor_flow import monitor_pull_requests, run_aider_agent

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _flow_from_source(flow_obj):
    entrypoint = f"flows/cursor_flow.py:{flow_obj.fn.__name__}"
    return flow_obj.from_source(source=str(PROJECT_ROOT), entrypoint=entrypoint)


def _deploy_flow(
    flow_obj,
    *,
    name: str,
    schedule: CronSchedule,
    parameters: dict,
    queue: str,
    pool: str,
) -> None:
    source_flow = _flow_from_source(flow_obj)
    source_flow.deploy(
        name=name,
        parameters=parameters,
        schedule=schedule,
        work_queue_name=queue,
        work_pool_name=pool,
        build=False,
        push=False,
        ignore_warnings=True,
        print_next_steps=False,
    )


def build_and_apply_deployments() -> None:
    runner_image = os.getenv("RUNNER_IMAGE", "cursor-runner:local")
    intake_cron = os.getenv("PREFECT_INTAKE_CRON", "*/10 * * * *")
    monitor_cron = os.getenv("PREFECT_MONITOR_CRON", "*/5 * * * *")
    work_queue = os.getenv("PREFECT_WORK_QUEUE", "aider-agent")
    work_pool = os.getenv("PREFECT_WORK_POOL") or os.getenv("PREFECT_DEFAULT_WORK_POOL_NAME")
    if not work_pool:
        raise RuntimeError(
            "Set PREFECT_WORK_POOL or PREFECT_DEFAULT_WORK_POOL_NAME so deployments know which pool to target."
        )
    _deploy_flow(
        run_aider_agent,
        name="jira-intake",
        parameters={"runner_image": runner_image},
        schedule=CronSchedule(cron=intake_cron, timezone="UTC"),
        queue=work_queue,
        pool=work_pool,
    )

    _deploy_flow(
        monitor_pull_requests,
        name="pr-monitor",
        parameters={"runner_image": runner_image},
        schedule=CronSchedule(cron=monitor_cron, timezone="UTC"),
        queue=work_queue,
        pool=work_pool,
    )


if __name__ == "__main__":
    build_and_apply_deployments()

