from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from app.auth import verify_admin_token
from app.db import get_session
from app.fathom import FathomClient
from app.models import ProcessedMeeting, User
from app.schemas import AddUserRequest, HealthResponse, MeetingDebugResponse, RetryResponse, RotateUserKeyRequest, UserResponse
from app.services import STAT_FAILED, STAT_PROCESSING, STAT_SUCCESS, process_meeting_payload
from app.config import get_settings

router = APIRouter(prefix="/admin", tags=["admin"], dependencies=[Depends(verify_admin_token)])
fathom_client = FathomClient()


def _to_user_response(user: User) -> UserResponse:
    return UserResponse(
        id=user.id,
        email=user.email,
        name=user.name,
        active=user.active,
        fathom_webhook_id=user.fathom_webhook_id,
        created_at=user.created_at,
    )


@router.post("/users", response_model=UserResponse)
async def add_user(payload: AddUserRequest, session: AsyncSession = Depends(get_session)) -> UserResponse:
    existing = await session.execute(select(User).where(User.email == payload.email))
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=409, detail="User email already exists")

    user = User(
        email=payload.email,
        name=payload.name,
        fathom_api_key_encrypted=payload.fathom_api_key,
        clickup_api_token_encrypted=payload.clickup_api_token,
        clickup_default_assignee_id=payload.clickup_default_assignee_id,
        active=True,
    )
    session.add(user)
    await session.commit()
    await session.refresh(user)

    settings = get_settings()
    webhook_url = f"{settings.public_url}/webhooks/fathom/{user.id}"
    try:
        webhook = await fathom_client.create_webhook(api_key=payload.fathom_api_key, destination_url=webhook_url)
        user.fathom_webhook_id = webhook.get("id")
        user.fathom_webhook_secret = webhook.get("secret")
        session.add(user)
        await session.commit()
        await session.refresh(user)
    except Exception as exc:
        await session.delete(user)
        await session.commit()
        raise HTTPException(status_code=502, detail=f"Failed registering Fathom webhook: {exc}")

    return _to_user_response(user)


@router.get("/users", response_model=list[UserResponse])
async def list_users(session: AsyncSession = Depends(get_session)) -> list[UserResponse]:
    rows = await session.execute(select(User).where(User.active.is_(True)))
    return [_to_user_response(u) for u in rows.scalars().all()]


@router.delete("/users/{user_id}")
async def remove_user(user_id: UUID, session: AsyncSession = Depends(get_session)) -> dict[str, str]:
    user = await session.get(User, user_id)
    if not user or not user.active:
        raise HTTPException(status_code=404, detail="User not found")

    if user.fathom_webhook_id:
        try:
            fathom_key = user.fathom_api_key_encrypted
            await fathom_client.delete_webhook(fathom_key, user.fathom_webhook_id)
        except Exception:
            pass

    user.active = False
    session.add(user)
    await session.commit()
    return {"status": "removed"}


@router.post("/users/{user_id}/rotate-key", response_model=UserResponse)
async def rotate_user_keys(
    user_id: UUID,
    payload: RotateUserKeyRequest,
    session: AsyncSession = Depends(get_session),
) -> UserResponse:
    user = await session.get(User, user_id)
    if not user or not user.active:
        raise HTTPException(status_code=404, detail="User not found")

    if payload.fathom_api_key:
        if user.fathom_webhook_id:
            try:
                old_key = user.fathom_api_key_encrypted
                await fathom_client.delete_webhook(old_key, user.fathom_webhook_id)
            except Exception:
                pass

        user.fathom_api_key_encrypted = payload.fathom_api_key
        settings = get_settings()
        webhook_url = f"{settings.public_url}/webhooks/fathom/{user.id}"
        webhook = await fathom_client.create_webhook(api_key=payload.fathom_api_key, destination_url=webhook_url)
        user.fathom_webhook_id = webhook.get("id")
        user.fathom_webhook_secret = webhook.get("secret")

    if payload.clickup_api_token:
        user.clickup_api_token_encrypted = payload.clickup_api_token
    if payload.clickup_default_assignee_id is not None:
        user.clickup_default_assignee_id = payload.clickup_default_assignee_id

    session.add(user)
    await session.commit()
    await session.refresh(user)
    return _to_user_response(user)


@router.get("/meetings", response_model=list[MeetingDebugResponse])
async def list_meetings(
    status: str = Query(default=STAT_FAILED),
    session: AsyncSession = Depends(get_session),
) -> list[MeetingDebugResponse]:
    rows = await session.execute(select(ProcessedMeeting).where(ProcessedMeeting.status == status))
    meetings = []
    for row in rows.scalars().all():
        payload = None
        if row.raw_payload:
            try:
                payload = json.loads(row.raw_payload)
            except json.JSONDecodeError:
                payload = None
        meetings.append(
            MeetingDebugResponse(
                recording_id=row.recording_id,
                user_id=row.user_id,
                status=row.status,
                error_message=row.error_message,
                title=row.title,
                processed_at=row.processed_at,
                raw_payload=payload,
            )
        )
    return meetings


@router.post("/meetings/{recording_id}/retry", response_model=RetryResponse)
async def retry_meeting(recording_id: int, session: AsyncSession = Depends(get_session)) -> RetryResponse:
    meeting = await session.get(ProcessedMeeting, recording_id)
    if not meeting:
        raise HTTPException(status_code=404, detail="Meeting not found")
    if not meeting.raw_payload:
        raise HTTPException(status_code=400, detail="No payload stored for retry")

    user = await session.get(User, meeting.user_id)
    if not user or not user.active:
        raise HTTPException(status_code=400, detail="User inactive or not found")

    now = datetime.now(UTC)
    stale_cutoff = now - timedelta(minutes=10)
    claim = await session.execute(
        text(
            """
            UPDATE processed_meetings
               SET status = :processing, error_message = NULL, processed_at = :now,
                   clickup_task_id = NULL
             WHERE recording_id = :rid
               AND (
                   status IN (:failed, :success)
                   OR (status = :processing AND processed_at < :stale_cutoff)
               )
            """
        ),
        {
            "processing": STAT_PROCESSING,
            "failed": STAT_FAILED,
            "success": STAT_SUCCESS,
            "rid": recording_id,
            "now": now,
            "stale_cutoff": stale_cutoff,
        },
    )
    await session.commit()
    if (claim.rowcount or 0) == 0:
        raise HTTPException(status_code=409, detail="Meeting is currently being processed")

    try:
        payload = json.loads(meeting.raw_payload)
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="Stored payload is invalid JSON")

    task_id = await process_meeting_payload(session, user, payload)
    return RetryResponse(status="retried", recording_id=recording_id, task_id=task_id)


@router.get("/health", response_model=HealthResponse)
async def health(session: AsyncSession = Depends(get_session)) -> HealthResponse:
    await session.execute(text("SELECT 1"))
    return HealthResponse(status="ok", db="ok")
