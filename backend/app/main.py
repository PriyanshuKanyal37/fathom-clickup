from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from typing import Any
from uuid import UUID

from fastapi import BackgroundTasks, Depends, FastAPI, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from app import fathom
from app.admin_routes import router as admin_router
from app.config import get_settings
from app.db import SessionLocal, get_session, init_db
from app.models import User
from app.schemas import WebhookResponse
from app.services import claim_recording, process_meeting_payload, periodic_reconciler

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    app.state.reconciler_task = asyncio.create_task(periodic_reconciler(SessionLocal))
    try:
        yield
    finally:
        app.state.reconciler_task.cancel()


app = FastAPI(title="Fathom ClickUp Automation", lifespan=lifespan)
app.include_router(admin_router)


async def _process_in_background(user_id: UUID, payload: dict[str, Any]) -> None:
    async with SessionLocal() as session:
        user = await session.get(User, user_id)
        if not user or not user.active:
            return
        try:
            await process_meeting_payload(session, user, payload)
        except Exception:
            logger.exception("Background processing failed for recording %s", payload.get("recording_id"))


@app.post("/webhooks/fathom/{user_id}", response_model=WebhookResponse)
async def handle_fathom_webhook(
    user_id: UUID,
    request: Request,
    background_tasks: BackgroundTasks,
    session: AsyncSession = Depends(get_session),
) -> WebhookResponse:
    user_row = await session.execute(select(User).where(User.id == user_id, User.active.is_(True)))
    user = user_row.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="Unknown user")

    raw_body = await request.body()
    settings = get_settings()
    is_valid = fathom.verify_signature(
        raw_body=raw_body,
        headers=request.headers,
        webhook_secret=user.fathom_webhook_secret or "",
        tolerance_seconds=settings.webhook_tolerance_seconds,
    )
    if not is_valid:
        raise HTTPException(status_code=403, detail="Invalid webhook signature")

    try:
        payload = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Malformed JSON payload")

    recording_id = payload.get("recording_id")
    if not recording_id:
        raise HTTPException(status_code=400, detail="Missing recording_id")

    claimed = await claim_recording(session, int(recording_id), user.id, payload)
    if not claimed:
        return WebhookResponse(status="already_processed")

    background_tasks.add_task(_process_in_background, user.id, payload)
    return WebhookResponse(status="accepted")
