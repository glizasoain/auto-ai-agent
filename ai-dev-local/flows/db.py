import json
import os
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import SQLAlchemyError
from urllib.parse import quote_plus

STATUS_NEW = "new"
STATUS_IN_PROGRESS = "in_progress"
STATUS_IN_REVIEW = "in_review"
STATUS_CHANGES_REQUESTED = "changes_requested"
STATUS_MERGED = "merged"
STATUS_FAILED = "failed"

ACTIVE_STATUSES = {
    STATUS_NEW,
    STATUS_IN_PROGRESS,
    STATUS_IN_REVIEW,
    STATUS_CHANGES_REQUESTED,
}
TERMINAL_STATUSES = {STATUS_MERGED, STATUS_FAILED}

SCHEMA_PATH = Path(__file__).resolve().parent.parent / "db" / "schema.sql"


def _mysql_settings() -> Dict[str, str]:
    host = os.getenv("MYSQL_HOST", "127.0.0.1")
    port = os.getenv("MYSQL_PORT", "3306")
    user = os.getenv("MYSQL_USER")
    password = os.getenv("MYSQL_PASSWORD")
    database = os.getenv("MYSQL_DB")

    if not all([user, password, database]):
        raise RuntimeError("Missing MYSQL_USER, MYSQL_PASSWORD, or MYSQL_DB environment variables.")

    return {
        "host": host,
        "port": port,
        "user": user,
        "password": password,
        "database": database,
    }


@lru_cache(maxsize=1)
def _get_engine() -> Engine:
    settings = _mysql_settings()
    password = quote_plus(settings["password"])
    url = (
        f"mysql+pymysql://{settings['user']}:{password}"
        f"@{settings['host']}:{settings['port']}/{settings['database']}"
    )
    return create_engine(url, pool_pre_ping=True, pool_recycle=3600, future=True)


def initialize_schema(log) -> None:
    if not SCHEMA_PATH.exists():
        raise RuntimeError(f"Schema file missing at {SCHEMA_PATH}")

    sql_text = SCHEMA_PATH.read_text()
    statements = [statement.strip() for statement in sql_text.split(";") if statement.strip()]

    engine = _get_engine()
    with engine.begin() as conn:
        for statement in statements:
            conn.exec_driver_sql(statement)

        try:
            conn.exec_driver_sql("CREATE INDEX idx_agent_runs_status ON agent_runs (status, updated_at)")
        except SQLAlchemyError as exc:
            # MySQL error code 1061 = duplicate key name
            if not getattr(exc, "orig", None) or getattr(exc.orig, "args", [None])[0] != 1061:
                raise

    if log:
        log.info("Ensured agent_runs schema is present.")


def _row_to_dict(row) -> Dict[str, Any]:
    data = dict(row)
    metadata = data.get("metadata")
    if isinstance(metadata, (str, bytes)):
        try:
            data["metadata"] = json.loads(metadata)
        except json.JSONDecodeError:
            data["metadata"] = {}
    else:
        data["metadata"] = metadata or {}
    return data


def get_active_issue_keys() -> set:
    if not ACTIVE_STATUSES:
        return set()
    placeholders = ", ".join(f":s{i}" for i in range(len(ACTIVE_STATUSES)))
    status_list = list(ACTIVE_STATUSES)
    sql = text(f"SELECT issue_key FROM agent_runs WHERE status IN ({placeholders})")
    params = {f"s{i}": status_list[i] for i in range(len(status_list))}

    with _get_engine().connect() as conn:
        rows = conn.execute(sql, params).scalars().all()
    return set(rows)


def claim_issue(issue_key: str, issue_summary: str, repo_url: str, branch_name: str) -> Optional[Dict[str, Any]]:
    engine = _get_engine()
    with engine.begin() as conn:
        row = (
            conn.execute(
                text("SELECT * FROM agent_runs WHERE issue_key = :issue_key FOR UPDATE"),
                {"issue_key": issue_key},
            )
            .mappings()
            .first()
        )

        if row and row["status"] not in TERMINAL_STATUSES:
            return None

        payload = {
            "issue_key": issue_key,
            "issue_summary": issue_summary,
            "repo_url": repo_url,
            "branch_name": branch_name,
            "status": STATUS_NEW,
        }

        if row:
            conn.execute(
                text(
                    """
                    UPDATE agent_runs
                    SET issue_summary = :issue_summary,
                        repo_url = :repo_url,
                        branch_name = :branch_name,
                        status = :status,
                        pr_number = NULL,
                        metadata = NULL,
                        last_runner_id = NULL
                    WHERE issue_key = :issue_key
                    """
                ),
                payload,
            )
        else:
            conn.execute(
                text(
                    """
                    INSERT INTO agent_runs (issue_key, issue_summary, repo_url, branch_name, status)
                    VALUES (:issue_key, :issue_summary, :repo_url, :branch_name, :status)
                    """
                ),
                payload,
            )

        refreshed = (
            conn.execute(
                text("SELECT * FROM agent_runs WHERE issue_key = :issue_key"),
                {"issue_key": issue_key},
            )
            .mappings()
            .one()
        )
        return _row_to_dict(refreshed)


def update_run(issue_key: str, **fields) -> None:
    if not fields:
        return

    metadata = fields.pop("metadata", None)
    if metadata is not None:
        fields["metadata"] = json.dumps(metadata)

    assignments = ", ".join(f"{column} = :{column}" for column in fields)
    sql = text(
        f"""
        UPDATE agent_runs
        SET {assignments}, updated_at = CURRENT_TIMESTAMP
        WHERE issue_key = :issue_key
        """
    )
    params = {"issue_key": issue_key, **fields}

    with _get_engine().begin() as conn:
        conn.execute(sql, params)


def get_runs_by_status(statuses: Iterable[str], limit: int = 20) -> List[Dict[str, Any]]:
    statuses = list(statuses)
    if not statuses:
        return []

    placeholders = ", ".join(f":s{i}" for i in range(len(statuses)))
    sql = text(
        f"""
        SELECT * FROM agent_runs
        WHERE status IN ({placeholders})
        ORDER BY updated_at DESC
        LIMIT :limit
        """
    )
    params = {f"s{i}": statuses[i] for i in range(len(statuses))}
    params["limit"] = limit

    with _get_engine().connect() as conn:
        rows = conn.execute(sql, params).mappings().all()
    return [_row_to_dict(row) for row in rows]


def get_run(issue_key: str) -> Optional[Dict[str, Any]]:
    sql = text("SELECT * FROM agent_runs WHERE issue_key = :issue_key")
    with _get_engine().connect() as conn:
        row = conn.execute(sql, {"issue_key": issue_key}).mappings().first()
    return _row_to_dict(row) if row else None

