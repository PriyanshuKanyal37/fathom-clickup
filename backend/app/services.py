from __future__ import annotations

import asyncio
import json
import logging
import re
from datetime import UTC, datetime, timedelta, timezone
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.clickup import ClickupClient
from app.config import get_settings
from app.llm import extract_action_items, generate_meeting_title, summarize_meeting, transcript_to_text
from app.models import User

logger = logging.getLogger(__name__)

STAT_PROCESSING = "processing"
STAT_SUCCESS = "success"
STAT_FAILED = "failed"

IST = timezone(timedelta(hours=5, minutes=30), name="IST")


def to_ist(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(IST)


def _parse_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    fixed = value.replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(fixed)
    except ValueError:
        return None


GENERIC_TITLE_RE = re.compile(r"^(impromptu|untitled|fathom meeting)\b", re.IGNORECASE)


def _fathom_title(payload: dict[str, Any]) -> str:
    return payload.get("title") or payload.get("meeting_title") or f"Fathom Meeting {payload.get('recording_id', '')}"


def _title_is_generic(title: str) -> bool:
    t = (title or "").strip()
    if not t:
        return True
    if GENERIC_TITLE_RE.match(t):
        return True
    if t.lower() in {"meeting", "google meet", "zoom meeting", "team meeting"}:
        return True
    return False


FATHOM_LINK_RE = re.compile(r"\[([^\]]+)\]\(https?://(?:[^)]*\.)?fathom\.video/[^)]*\)")


def _strip_fathom_timestamp_links(md: str | None) -> str:
    if not md:
        return ""
    return FATHOM_LINK_RE.sub(r"\1", md)


def _is_good_fathom_summary(payload: dict[str, Any]) -> bool:
    summary = payload.get("default_summary") or {}
    markdown = (summary.get("markdown_formatted") or "").strip()
    template = (summary.get("template_name") or "").lower()
    if not markdown:
        return False
    if template in {"chronological"}:
        return False
    return len(markdown) >= 40


def _comprehensive_attendee_names(payload: dict[str, Any]) -> list[str]:
    seen_keys: set[str] = set()
    ordered: list[str] = []

    def add(name: str | None) -> None:
        if not name:
            return
        clean = name.strip()
        if not clean:
            return
        key = clean.lower()
        if key == "unknown" or key in seen_keys:
            return
        seen_keys.add(key)
        ordered.append(clean)

    for a in payload.get("calendar_invitees") or []:
        add(a.get("name"))
    for row in payload.get("transcript") or []:
        add((row.get("speaker") or {}).get("display_name"))
    add((payload.get("recorded_by") or {}).get("name"))
    for item in payload.get("action_items") or []:
        add((item.get("assignee") or {}).get("name"))
    return ordered


def _format_parent_description(
    payload: dict[str, Any],
    summary_md: str,
    meeting_date: datetime | None = None,
) -> str:
    attendee_names = _comprehensive_attendee_names(payload)
    share_url = payload.get("share_url") or payload.get("url") or ""
    recorded_by = (payload.get("recorded_by") or {}).get("name") or "Unknown"

    lines = ["## Meeting Meta", f"- Recorded by: {recorded_by}"]
    meeting_date_ist = to_ist(meeting_date)
    if meeting_date_ist:
        lines.append(f"- Date: {meeting_date_ist.strftime('%a, %d %b %Y, %I:%M %p IST')}")
    lines.extend(
        [
            f"- Attendees: {', '.join(attendee_names) if attendee_names else 'N/A'}",
            f"- Recording link: {share_url or 'N/A'}",
            "",
            "## Summary",
            summary_md or "Summary unavailable.",
        ]
    )
    return "\n".join(lines)


def _normalize_name(name: str | None) -> str:
    if not name:
        return ""
    return " ".join(name.strip().lower().split())


def _build_content_text(payload: dict[str, Any], summary_md: str) -> str:
    parts: list[str] = [summary_md or ""]
    for row in payload.get("transcript") or []:
        text = row.get("text") or ""
        if text:
            parts.append(text)
    for ai in payload.get("action_items") or []:
        desc = ai.get("description") or ""
        if desc:
            parts.append(desc)
        assignee_name = (ai.get("assignee") or {}).get("name")
        if assignee_name:
            parts.append(assignee_name)
    return "\n".join(parts)


def _match_content_to_members(content: str, members: list[dict[str, Any]]) -> list[int]:
    if not content or not members:
        return []

    first_name_counts: dict[str, int] = {}
    for m in members:
        full = _normalize_name(m.get("username"))
        if not full:
            continue
        first = full.split()[0]
        first_name_counts[first] = first_name_counts.get(first, 0) + 1

    matched: list[int] = []
    seen: set[int] = set()
    for m in members:
        mid = m.get("id")
        if mid is None:
            continue
        full = _normalize_name(m.get("username"))
        if not full:
            continue
        first = full.split()[0]
        hit = False
        if re.search(rf"\b{re.escape(full)}\b", content, re.IGNORECASE):
            hit = True
        elif first_name_counts.get(first, 0) == 1 and re.search(
            rf"\b{re.escape(first)}\b", content, re.IGNORECASE
        ):
            hit = True
        if hit and int(mid) not in seen:
            matched.append(int(mid))
            seen.add(int(mid))
    return matched


def _match_attendees_to_members(
    attendees: list[dict[str, Any]],
    members: list[dict[str, Any]],
) -> list[int]:
    email_to_id: dict[str, int] = {}
    name_to_id: dict[str, int] = {}
    for m in members:
        mid = m.get("id")
        if mid is None:
            continue
        email = (m.get("email") or "").strip().lower()
        if email:
            email_to_id[email] = int(mid)
        name = _normalize_name(m.get("username"))
        if name:
            name_to_id[name] = int(mid)

    matched: list[int] = []
    seen: set[int] = set()
    for attendee in attendees:
        email = (attendee.get("email") or "").strip().lower()
        name = _normalize_name(attendee.get("name"))
        member_id: int | None = None
        if email and email in email_to_id:
            member_id = email_to_id[email]
        elif name and name in name_to_id:
            member_id = name_to_id[name]
        if member_id is not None and member_id not in seen:
            matched.append(member_id)
            seen.add(member_id)
    return matched


def _json_dump(value: Any) -> str | None:
    if value is None:
        return None
    return json.dumps(value, ensure_ascii=False)


async def claim_recording(session: AsyncSession, recording_id: int, user_id: UUID, payload: dict[str, Any]) -> bool:
    stmt = text(
        """
        INSERT INTO processed_meetings (
            recording_id,
            user_id,
            status,
            share_url,
            recording_start_time,
            recording_end_time,
            transcript_json,
            default_summary_json,
            action_items_json,
            calendar_invitees_json,
            recorded_by_json,
            raw_payload,
            processed_at
        )
        VALUES (
            :rid,
            :uid,
            :status,
            :share_url,
            :recording_start_time,
            :recording_end_time,
            :transcript_json,
            :default_summary_json,
            :action_items_json,
            :calendar_invitees_json,
            :recorded_by_json,
            :raw_payload,
            :processed_at
        )
        ON CONFLICT (recording_id) DO NOTHING
        """
    )
    result = await session.execute(
        stmt,
        {
            "rid": recording_id,
            "uid": str(user_id),
            "status": STAT_PROCESSING,
            "share_url": payload.get("share_url") or payload.get("url"),
            "recording_start_time": _parse_datetime(payload.get("recording_start_time")),
            "recording_end_time": _parse_datetime(payload.get("recording_end_time")),
            "transcript_json": _json_dump(payload.get("transcript")),
            "default_summary_json": _json_dump(payload.get("default_summary")),
            "action_items_json": _json_dump(payload.get("action_items")),
            "calendar_invitees_json": _json_dump(payload.get("calendar_invitees")),
            "recorded_by_json": _json_dump(payload.get("recorded_by")),
            "raw_payload": _json_dump(payload),
            "processed_at": datetime.now(UTC),
        },
    )
    await session.commit()
    return (result.rowcount or 0) > 0


async def mark_failed(session: AsyncSession, recording_id: int, error_message: str) -> None:
    stmt = text(
        """
        UPDATE processed_meetings
           SET status = :status, error_message = :error_message, completed_at = :completed_at
         WHERE recording_id = :rid
        """
    )
    await session.execute(
        stmt,
        {
            "status": STAT_FAILED,
            "error_message": error_message[:4000],
            "completed_at": datetime.now(UTC),
            "rid": recording_id,
        },
    )
    await session.commit()


async def mark_success(
    session: AsyncSession,
    recording_id: int,
    task_id: str,
    title: str,
    meeting_date: datetime | None,
    summary_source: str,
    action_item_count: int,
    payload: dict[str, Any] | None = None,
) -> None:
    stmt = text(
        """
        UPDATE processed_meetings
           SET status = :status,
               clickup_task_id = :task_id,
               title = :title,
               meeting_date = :meeting_date,
               summary_source = :summary_source,
               action_item_count = :action_item_count,
               share_url = COALESCE(:share_url, share_url),
               recording_start_time = COALESCE(:recording_start_time, recording_start_time),
               recording_end_time = COALESCE(:recording_end_time, recording_end_time),
               transcript_json = COALESCE(:transcript_json, transcript_json),
               default_summary_json = COALESCE(:default_summary_json, default_summary_json),
               action_items_json = COALESCE(:action_items_json, action_items_json),
               calendar_invitees_json = COALESCE(:calendar_invitees_json, calendar_invitees_json),
               recorded_by_json = COALESCE(:recorded_by_json, recorded_by_json),
               raw_payload = COALESCE(:raw_payload, raw_payload),
               completed_at = :completed_at
         WHERE recording_id = :rid
        """
    )
    payload = payload or {}
    await session.execute(
        stmt,
        {
            "status": STAT_SUCCESS,
            "task_id": task_id,
            "title": title,
            "meeting_date": meeting_date,
            "summary_source": summary_source,
            "action_item_count": action_item_count,
            "share_url": payload.get("share_url") or payload.get("url"),
            "recording_start_time": _parse_datetime(payload.get("recording_start_time")),
            "recording_end_time": _parse_datetime(payload.get("recording_end_time")),
            "transcript_json": _json_dump(payload.get("transcript")),
            "default_summary_json": _json_dump(payload.get("default_summary")),
            "action_items_json": _json_dump(payload.get("action_items")),
            "calendar_invitees_json": _json_dump(payload.get("calendar_invitees")),
            "recorded_by_json": _json_dump(payload.get("recorded_by")),
            "raw_payload": _json_dump(payload) if payload else None,
            "completed_at": datetime.now(UTC),
            "rid": recording_id,
        },
    )
    await session.commit()


async def process_meeting_payload(
    session: AsyncSession,
    user: User,
    payload: dict[str, Any],
    clickup: ClickupClient | None = None,
) -> str:
    clickup_client = clickup or ClickupClient()
    settings = get_settings()

    recording_id = int(payload["recording_id"])
    fathom_title = _fathom_title(payload)
    meeting_date = _parse_datetime(payload.get("recording_start_time") or payload.get("created_at"))

    attendees = payload.get("calendar_invitees") or []
    meeting_date_ist = to_ist(meeting_date)
    meeting_date_iso = meeting_date_ist.date().isoformat() if meeting_date_ist else None

    existing_task_id = (
        await session.execute(
            text("SELECT clickup_task_id FROM processed_meetings WHERE recording_id = :rid"),
            {"rid": recording_id},
        )
    ).scalar()
    if existing_task_id:
        return str(existing_task_id)

    try:
        if _is_good_fathom_summary(payload):
            summary_md = _strip_fathom_timestamp_links(payload["default_summary"]["markdown_formatted"])
            summary_source = "fathom_ai"
        else:
            summary_md = await summarize_meeting(
                payload.get("transcript") or [],
                meeting_title=fathom_title,
                meeting_date=meeting_date_iso,
                attendees=attendees,
            )
            summary_source = "llm"

        title = fathom_title
        if _title_is_generic(fathom_title):
            title_context = summary_md or transcript_to_text(payload.get("transcript") or [])
            generated = await generate_meeting_title(title_context) if title_context.strip() else ""
            if generated and not _title_is_generic(generated):
                title = generated
                logger.info("Replaced generic Fathom title %r with LLM title %r", fathom_title, title)

        provided_action_items = payload.get("action_items") or []
        if provided_action_items:
            action_items = [
                {
                    "description": item.get("description") or item.get("text") or "Action item",
                    "assignee_name": (item.get("assignee") or {}).get("name"),
                    "deadline": item.get("deadline"),
                }
                for item in provided_action_items
            ]
            action_items = [a for a in action_items if a["description"]]
        else:
            action_items = await extract_action_items(
                payload.get("transcript") or [],
                attendees,
                meeting_date=meeting_date_iso,
            )

        description = _format_parent_description(payload, summary_md, meeting_date=meeting_date)
        clickup_token = user.clickup_api_token_encrypted

        assignee_ids: list[int] = []
        if settings.clickup_workspace_id:
            try:
                members = await clickup_client.list_workspace_members(
                    clickup_token=clickup_token,
                    workspace_id=settings.clickup_workspace_id,
                )
                from_invitees = _match_attendees_to_members(attendees, members)
                content_text = _build_content_text(payload, summary_md)
                from_content = _match_content_to_members(content_text, members)
                combined: list[int] = []
                seen: set[int] = set()
                for mid in from_invitees + from_content:
                    if mid not in seen:
                        combined.append(mid)
                        seen.add(mid)
                assignee_ids = combined
            except Exception as exc:
                logger.warning("Could not fetch ClickUp members for recording %s: %s", recording_id, exc)

        recording_end = _parse_datetime(payload.get("recording_end_time"))
        start_ms = int(meeting_date.timestamp() * 1000) if meeting_date else None
        due_ms = int(recording_end.timestamp() * 1000) if recording_end else start_ms

        task_id = await clickup_client.create_meeting_task(
            clickup_token=clickup_token,
            list_id=settings.clickup_list_id,
            title=title,
            markdown_description=description,
            assignees=assignee_ids or None,
            start_date_ms=start_ms,
            due_date_ms=due_ms,
        )

        if settings.clickup_date_field_id and start_ms is not None:
            try:
                await clickup_client.set_task_custom_field(
                    clickup_token=clickup_token,
                    task_id=task_id,
                    field_id=settings.clickup_date_field_id,
                    value=start_ms,
                    value_options={"time": True},
                )
            except Exception as exc:
                logger.warning("Could not set custom date field on task %s: %s", task_id, exc)

        for item in action_items:
            subtask_desc = ""
            if item.get("deadline"):
                subtask_desc = f"Deadline: {item['deadline']}"
            await clickup_client.create_subtask(
                clickup_token=clickup_token,
                list_id=settings.clickup_list_id,
                parent_task_id=task_id,
                title=item["description"],
                markdown_description=subtask_desc,
                assignee_id=user.clickup_default_assignee_id,
            )

        await mark_success(
            session=session,
            recording_id=recording_id,
            task_id=task_id,
            title=title,
            meeting_date=meeting_date,
            summary_source=summary_source,
            action_item_count=len(action_items),
            payload=payload,
        )
        return task_id
    except Exception as exc:
        await mark_failed(session, recording_id, str(exc))
        raise


async def periodic_reconciler(session_factory, interval_seconds: int = 300) -> None:
    while True:
        try:
            async with session_factory() as session:
                cutoff = datetime.now(UTC) - timedelta(minutes=10)
                candidates = (
                    await session.execute(
                        text(
                            """
                            SELECT recording_id
                              FROM processed_meetings
                             WHERE status = :status AND processed_at < :cutoff
                             LIMIT 25
                            """
                        ),
                        {"status": STAT_PROCESSING, "cutoff": cutoff},
                    )
                ).scalars().all()

                for recording_id in candidates:
                    claim_result = await session.execute(
                        text(
                            """
                            UPDATE processed_meetings
                               SET processed_at = :now
                             WHERE recording_id = :rid
                               AND status = :status
                               AND processed_at < :cutoff
                            """
                        ),
                        {
                            "now": datetime.now(UTC),
                            "rid": recording_id,
                            "status": STAT_PROCESSING,
                            "cutoff": cutoff,
                        },
                    )
                    await session.commit()
                    if (claim_result.rowcount or 0) == 0:
                        continue

                    row = (
                        await session.execute(
                            text("SELECT user_id, raw_payload FROM processed_meetings WHERE recording_id = :rid"),
                            {"rid": recording_id},
                        )
                    ).mappings().first()
                    if not row or not row["raw_payload"]:
                        await mark_failed(session, recording_id, "Missing raw payload for retry")
                        continue

                    user = await session.get(User, row["user_id"])
                    if not user or not user.active:
                        await mark_failed(session, recording_id, "User inactive/not found during retry")
                        continue

                    try:
                        payload = json.loads(row["raw_payload"])
                        await process_meeting_payload(session, user, payload)
                    except Exception:
                        pass
        except Exception:
            pass
        await asyncio.sleep(interval_seconds)
