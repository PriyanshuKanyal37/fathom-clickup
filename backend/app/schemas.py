from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field


class AddUserRequest(BaseModel):
    email: str
    name: str
    fathom_api_key: str
    clickup_api_token: str
    clickup_default_assignee_id: int | None = None


class RotateUserKeyRequest(BaseModel):
    fathom_api_key: str | None = None
    clickup_api_token: str | None = None
    clickup_default_assignee_id: int | None = None


class UserResponse(BaseModel):
    id: UUID
    email: str
    name: str
    active: bool
    fathom_webhook_id: str | None = None
    created_at: datetime


class ActionItem(BaseModel):
    description: str
    assignee_name: str | None = None
    deadline: str | None = None


class RetryResponse(BaseModel):
    status: str
    recording_id: int
    task_id: str | None = None


class HealthResponse(BaseModel):
    status: str
    db: str


class WebhookResponse(BaseModel):
    status: str
    task_id: str | None = None


class MeetingDebugResponse(BaseModel):
    recording_id: int
    user_id: UUID
    status: str
    error_message: str | None = None
    title: str | None = None
    processed_at: datetime
    raw_payload: dict[str, Any] | None = None

