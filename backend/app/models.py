from __future__ import annotations

from datetime import UTC, datetime
from typing import Optional
from uuid import UUID, uuid4

from sqlalchemy import DateTime
from sqlmodel import Field, SQLModel


def now_utc() -> datetime:
    return datetime.now(UTC)


class User(SQLModel, table=True):
    __tablename__ = "users"

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    email: str = Field(index=True, unique=True)
    name: str
    fathom_api_key_encrypted: str
    clickup_api_token_encrypted: str
    clickup_default_assignee_id: Optional[int] = None
    fathom_webhook_id: Optional[str] = None
    fathom_webhook_secret: Optional[str] = None
    active: bool = Field(default=True, index=True)
    created_at: datetime = Field(
        default_factory=now_utc,
        sa_type=DateTime(timezone=True),
        nullable=False,
    )


class ProcessedMeeting(SQLModel, table=True):
    __tablename__ = "processed_meetings"

    recording_id: int = Field(primary_key=True)
    user_id: UUID = Field(foreign_key="users.id", index=True)
    clickup_task_id: Optional[str] = None
    title: Optional[str] = None
    meeting_date: Optional[datetime] = Field(
        default=None, sa_type=DateTime(timezone=True)
    )
    status: str = Field(index=True)
    error_message: Optional[str] = None
    summary_source: Optional[str] = None
    action_item_count: Optional[int] = None
    share_url: Optional[str] = None
    recording_start_time: Optional[datetime] = Field(
        default=None, sa_type=DateTime(timezone=True)
    )
    recording_end_time: Optional[datetime] = Field(
        default=None, sa_type=DateTime(timezone=True)
    )
    transcript_json: Optional[str] = None
    default_summary_json: Optional[str] = None
    action_items_json: Optional[str] = None
    calendar_invitees_json: Optional[str] = None
    recorded_by_json: Optional[str] = None
    raw_payload: Optional[str] = None
    processed_at: datetime = Field(
        default_factory=now_utc,
        sa_type=DateTime(timezone=True),
        nullable=False,
    )
    completed_at: Optional[datetime] = Field(
        default=None, sa_type=DateTime(timezone=True)
    )


class ClickupMember(SQLModel, table=True):
    __tablename__ = "clickup_members"

    email: str = Field(primary_key=True)
    clickup_user_id: int
    full_name: Optional[str] = None
    refreshed_at: datetime = Field(
        default_factory=now_utc,
        sa_type=DateTime(timezone=True),
        nullable=False,
    )
