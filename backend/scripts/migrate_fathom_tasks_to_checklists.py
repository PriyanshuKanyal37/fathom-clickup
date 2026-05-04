from __future__ import annotations

import argparse
import asyncio
import os
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
from dotenv import load_dotenv
from sqlalchemy import text

BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR))

from app.db import SessionLocal
from app.config import get_settings


BASE_URL = "https://api.clickup.com/api/v2"


@dataclass
class ParentTask:
    id: str
    name: str
    sort_ms: int
    subtasks: list[dict[str, Any]]
    checklists: list[dict[str, Any]]


def _ms(value: Any) -> int | None:
    if value in (None, "", 0, "0"):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _task_sort_ms(task: dict[str, Any]) -> int:
    for key in ("start_date", "due_date", "date_created"):
        parsed = _ms(task.get(key))
        if parsed is not None:
            return parsed
    return 0


def _strip_existing_fm_prefix(name: str) -> str:
    parts = name.split(" - ", 1)
    if len(parts) == 2 and parts[0].startswith("FM-") and parts[0][3:].isdigit():
        return parts[1].strip()
    parts = name.split(" — ", 1)
    if len(parts) == 2 and parts[0].startswith("FM-") and parts[0][3:].isdigit():
        return parts[1].strip()
    return name.strip()


def _subtask_item_name(task_ref: str, index: int, subtask: dict[str, Any]) -> str:
    title = _strip_existing_fm_prefix(str(subtask.get("name") or "Action item"))
    return f"{task_ref}.{index} - {title}"


def _action_items_checklist(task: ParentTask) -> dict[str, Any] | None:
    for checklist in task.checklists:
        if (checklist.get("name") or "").strip().lower() == "action items":
            return checklist
    return None


async def _request(
    client: httpx.AsyncClient,
    method: str,
    path: str,
    token: str,
    json: dict[str, Any] | None = None,
    params: dict[str, Any] | None = None,
) -> dict[str, Any]:
    response = await client.request(
        method,
        f"{BASE_URL}{path}",
        headers={"Authorization": token, "Content-Type": "application/json"},
        json=json,
        params=params,
    )
    response.raise_for_status()
    if not response.content:
        return {}
    return response.json()


async def _get_clickup_token() -> str:
    async with SessionLocal() as session:
        token = (
            await session.execute(
                text(
                    """
                    SELECT clickup_api_token_encrypted
                      FROM users
                     WHERE active IS TRUE
                     LIMIT 1
                    """
                )
            )
        ).scalar_one_or_none()
    if not token:
        raise RuntimeError("No active ClickUp token found in users table")
    return str(token)


async def _sync_sequence(value: int) -> None:
    async with SessionLocal() as session:
        await session.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS clickup_sequences (
                    prefix VARCHAR NOT NULL PRIMARY KEY,
                    value INTEGER NOT NULL
                )
                """
            )
        )
        await session.execute(
            text(
                """
                INSERT INTO clickup_sequences (prefix, value)
                VALUES ('FM', :value)
                ON CONFLICT(prefix) DO UPDATE SET value = excluded.value
                """
            ),
            {"value": value},
        )
        await session.commit()


async def _fetch_all_tasks(client: httpx.AsyncClient, token: str, list_id: str) -> list[dict[str, Any]]:
    tasks: list[dict[str, Any]] = []
    page = 0
    while True:
        data = await _request(
            client,
            "GET",
            f"/list/{list_id}/task",
            token,
            params={"include_closed": "true", "subtasks": "true", "page": page},
        )
        batch = data.get("tasks", []) or []
        tasks.extend(batch)
        if len(batch) < 100:
            return tasks
        page += 1


async def _fetch_task_detail(client: httpx.AsyncClient, token: str, task_id: str) -> dict[str, Any]:
    return await _request(
        client,
        "GET",
        f"/task/{task_id}",
        token,
        params={"include_subtasks": "true"},
    )


async def _load_parent_tasks(client: httpx.AsyncClient, token: str, list_id: str) -> list[ParentTask]:
    tasks = await _fetch_all_tasks(client, token, list_id)
    subtask_map: dict[str, list[dict[str, Any]]] = {}
    parent_ids: list[str] = []
    task_by_id: dict[str, dict[str, Any]] = {}

    for task in tasks:
        task_id = str(task.get("id") or "")
        if not task_id:
            continue
        task_by_id[task_id] = task
        parent = task.get("parent")
        if isinstance(parent, dict):
            parent_id = str(parent.get("id") or "")
        else:
            parent_id = str(parent or "")
        if parent_id:
            subtask_map.setdefault(parent_id, []).append(task)
        else:
            parent_ids.append(task_id)

    parents: list[ParentTask] = []
    for task_id in parent_ids:
        detail = await _fetch_task_detail(client, token, task_id)
        task = task_by_id[task_id]
        detail_subtasks = detail.get("subtasks") or []
        parents.append(
            ParentTask(
                id=task_id,
                name=str(detail.get("name") or task.get("name") or ""),
                sort_ms=_task_sort_ms(detail or task),
                subtasks=detail_subtasks or subtask_map.get(task_id, []),
                checklists=detail.get("checklists") or [],
            )
        )
    return sorted(parents, key=lambda p: (p.sort_ms, p.id))


async def migrate(dry_run: bool) -> None:
    settings = get_settings()
    list_id = settings.clickup_list_id
    if not list_id:
        raise RuntimeError("CLICKUP_LIST_ID is required")

    token = await _get_clickup_token()
    async with httpx.AsyncClient(timeout=30) as client:
        parents = await _load_parent_tasks(client, token, list_id)

        print(f"Fathom parents: {len(parents)}")
        print(f"Fathom subtasks found: {sum(len(p.subtasks) for p in parents)}")
        if dry_run:
            for index, parent in enumerate(parents, 1):
                dt = datetime.fromtimestamp(parent.sort_ms / 1000, tz=UTC).date() if parent.sort_ms else "unknown"
                print(f"DRY FM-{index}: {dt} | {len(parent.subtasks)} subtasks | {parent.name[:90]}")
            return

        for index, parent in enumerate(parents, 1):
            task_ref = f"FM-{index}"
            new_name = f"{task_ref} - {_strip_existing_fm_prefix(parent.name)}"

            await _request(client, "PUT", f"/task/{parent.id}", token, json={"name": new_name})

            checklist = _action_items_checklist(parent)
            if parent.subtasks and checklist:
                checklist_id = str(checklist.get("id") or "")
                existing_item_names = {
                    str(item.get("name") or "").strip()
                    for item in checklist.get("items", []) or []
                }
            elif parent.subtasks:
                checklist_response = await _request(
                    client,
                    "POST",
                    f"/task/{parent.id}/checklist",
                    token,
                    json={"name": "Action Items"},
                )
                checklist_id = str(
                    (checklist_response.get("checklist") or {}).get("id")
                    or checklist_response.get("id")
                    or ""
                )
                existing_item_names = set()
                if not checklist_id:
                    raise RuntimeError(f"Checklist creation failed for {parent.id}")
            else:
                checklist_id = None
                existing_item_names = set()

            if checklist_id:
                for item_index, subtask in enumerate(parent.subtasks, 1):
                    item_name = _subtask_item_name(task_ref, item_index, subtask)
                    if item_name in existing_item_names:
                        continue
                    await _request(
                        client,
                        "POST",
                        f"/checklist/{checklist_id}/checklist_item",
                        token,
                        json={
                            "name": item_name,
                            "orderindex": item_index - 1,
                        },
                    )

            for subtask in parent.subtasks:
                subtask_id = str(subtask.get("id") or "")
                if subtask_id:
                    await _request(client, "DELETE", f"/task/{subtask_id}", token)

            print(f"{task_ref}: renamed, {len(parent.subtasks)} subtasks converted")

        await _sync_sequence(len(parents))
        print(f"Sequence synced: FM={len(parents)}; next new meeting will be FM-{len(parents) + 1}")


def main() -> None:
    os.chdir(BACKEND_DIR)
    load_dotenv(BACKEND_DIR / ".env")

    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    asyncio.run(migrate(dry_run=args.dry_run))


if __name__ == "__main__":
    main()
