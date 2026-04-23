from __future__ import annotations

import asyncio
from typing import Any

import httpx

CLICKUP_BASE_URL = "https://api.clickup.com/api/v2"


class ClickupClient:
    def __init__(self, timeout_seconds: int = 25) -> None:
        self.timeout_seconds = timeout_seconds

    async def _request_with_retry(
        self,
        method: str,
        url: str,
        token: str,
        json: dict[str, Any] | None = None,
        retries: int = 3,
    ) -> dict[str, Any]:
        headers = {"Authorization": token, "Content-Type": "application/json"}
        last_error: Exception | None = None
        for attempt in range(retries):
            try:
                async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
                    response = await client.request(method, url, headers=headers, json=json)
                if response.status_code in (429, 500, 502, 503, 504):
                    await asyncio.sleep(2**attempt)
                    continue
                response.raise_for_status()
                return response.json()
            except (httpx.HTTPError, httpx.TimeoutException) as exc:
                last_error = exc
                if attempt < retries - 1:
                    await asyncio.sleep(2**attempt)
                    continue
                raise
        if last_error:
            raise last_error
        raise RuntimeError("Unknown ClickUp request error")

    async def list_workspace_members(
        self,
        clickup_token: str,
        workspace_id: str,
    ) -> list[dict[str, Any]]:
        data = await self._request_with_retry(
            method="GET",
            url=f"{CLICKUP_BASE_URL}/team",
            token=clickup_token,
        )
        members: list[dict[str, Any]] = []
        for team in data.get("teams", []) or []:
            if str(team.get("id")) != str(workspace_id):
                continue
            for entry in team.get("members", []) or []:
                user = entry.get("user") or {}
                if not user.get("id"):
                    continue
                members.append(
                    {
                        "id": int(user["id"]),
                        "username": user.get("username") or "",
                        "email": user.get("email") or "",
                    }
                )
        return members

    async def create_meeting_task(
        self,
        clickup_token: str,
        list_id: str,
        title: str,
        markdown_description: str,
        assignees: list[int] | None = None,
        start_date_ms: int | None = None,
        due_date_ms: int | None = None,
    ) -> str:
        payload: dict[str, Any] = {"name": title, "markdown_description": markdown_description}
        if assignees:
            payload["assignees"] = assignees
        if start_date_ms is not None:
            payload["start_date"] = start_date_ms
            payload["start_date_time"] = True
        if due_date_ms is not None:
            payload["due_date"] = due_date_ms
            payload["due_date_time"] = True
        data = await self._request_with_retry(
            method="POST",
            url=f"{CLICKUP_BASE_URL}/list/{list_id}/task",
            token=clickup_token,
            json=payload,
        )
        return str(data["id"])

    async def set_task_custom_field(
        self,
        clickup_token: str,
        task_id: str,
        field_id: str,
        value: Any,
        value_options: dict[str, Any] | None = None,
    ) -> None:
        body: dict[str, Any] = {"value": value}
        if value_options:
            body["value_options"] = value_options
        await self._request_with_retry(
            method="POST",
            url=f"{CLICKUP_BASE_URL}/task/{task_id}/field/{field_id}",
            token=clickup_token,
            json=body,
        )

    async def create_subtask(
        self,
        clickup_token: str,
        list_id: str,
        parent_task_id: str,
        title: str,
        markdown_description: str = "",
        assignee_id: int | None = None,
    ) -> str:
        payload: dict[str, Any] = {
            "name": title,
            "parent": parent_task_id,
            "markdown_description": markdown_description,
        }
        if assignee_id is not None:
            payload["assignees"] = [assignee_id]
        data = await self._request_with_retry(
            method="POST",
            url=f"{CLICKUP_BASE_URL}/list/{list_id}/task",
            token=clickup_token,
            json=payload,
        )
        return str(data["id"])
