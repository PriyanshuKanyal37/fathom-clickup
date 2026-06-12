import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlmodel import SQLModel

from app.models import User
from app.services import claim_recording, process_meeting_payload


class RecordingClickupClient:
    def __init__(self) -> None:
        self.created_tasks = []
        self.renamed_tasks = []
        self.created_checklists = []
        self.created_checklist_items = []
        self.created_subtasks = []
        self.uploaded_attachments = []

    async def create_meeting_task(self, **kwargs):
        task_id = f"task-{len(self.created_tasks) + 1}"
        self.created_tasks.append({**kwargs, "task_id": task_id})
        return task_id

    async def rename_task(self, **kwargs):
        self.renamed_tasks.append(kwargs)

    async def create_checklist(self, **kwargs):
        checklist_id = f"checklist-{len(self.created_checklists) + 1}"
        self.created_checklists.append({**kwargs, "checklist_id": checklist_id})
        return checklist_id

    async def create_checklist_item(self, **kwargs):
        self.created_checklist_items.append(kwargs)

    async def create_subtask(self, **kwargs):
        self.created_subtasks.append(kwargs)
        return f"subtask-{len(self.created_subtasks) + 1}"

    async def upload_task_attachment(self, **kwargs):
        self.uploaded_attachments.append(kwargs)


class RecordingFathomClient:
    def __init__(self, transcript):
        self.transcript = transcript
        self.requested_recording_ids = []

    async def get_transcript(self, api_key: str, recording_id: int):
        self.requested_recording_ids.append((api_key, recording_id))
        return self.transcript


def _payload(recording_id: int, title: str, action_text: str) -> dict:
    return {
        "recording_id": recording_id,
        "title": title,
        "recording_start_time": "2026-05-04T10:00:00Z",
        "recording_end_time": "2026-05-04T10:30:00Z",
        "share_url": f"https://fathom.video/share/{recording_id}",
        "recorded_by": {"name": "Priyanshu Kanyal"},
        "calendar_invitees": [{"name": "Priyanshu Kanyal", "email": "priyanshu@theladder.ai"}],
        "default_summary": {
            "template_name": "standard",
            "markdown_formatted": "## Meeting Purpose\nDiscussed ClickUp automation and follow-up execution.",
        },
        "action_items": [
            {
                "description": action_text,
                "assignee": {"name": "Priyanshu Kanyal", "email": "priyanshu@theladder.ai"},
                "deadline": "2026-05-08",
            }
        ],
    }


def _transcript() -> list[dict]:
    return [
        {
            "timestamp": "00:00:03",
            "speaker": {
                "display_name": "Priyanshu Kanyal",
                "matched_calendar_invitee_email": "priyanshu@theladder.ai",
            },
            "text": "We need to validate the transcript attachment.",
        },
        {
            "timestamp": "00:00:09",
            "speaker": {
                "display_name": "Priyanshu Kanyal",
                "matched_calendar_invitee_email": "priyanshu@theladder.ai",
            },
            "text": "The attachment should be a plain text file.",
        },
    ]


@pytest.mark.asyncio
async def test_fathom_meetings_get_fm_ids_and_action_items_as_checklists():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", future=True)
    session_maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.create_all)

    user_id = uuid.uuid4()
    async with session_maker() as session:
        user = User(
            id=user_id,
            email="priyanshu@theladder.ai",
            name="Priyanshu Kanyal",
            fathom_api_key_encrypted="fathom-key",
            clickup_api_token_encrypted="clickup-token",
            active=True,
        )
        session.add(user)
        await session.commit()

        first_payload = _payload(7001, "ClickUp Automation Review", "Move Fathom actions into checklist")
        second_payload = _payload(7002, "Fathom Pipeline Review", "Verify deployment pipeline")
        await claim_recording(session, 7001, user_id, first_payload)
        await claim_recording(session, 7002, user_id, second_payload)

        clickup = RecordingClickupClient()
        fathom = RecordingFathomClient([])
        first_task_id = await process_meeting_payload(session, user, first_payload, clickup=clickup, fathom=fathom)
        second_task_id = await process_meeting_payload(session, user, second_payload, clickup=clickup, fathom=fathom)

    assert first_task_id == "task-1"
    assert second_task_id == "task-2"
    assert fathom.requested_recording_ids == [("fathom-key", 7001), ("fathom-key", 7002)]
    assert clickup.renamed_tasks == [
        {
            "clickup_token": "clickup-token",
            "task_id": "task-1",
            "title": "FM-1 - ClickUp Automation Review",
        },
        {
            "clickup_token": "clickup-token",
            "task_id": "task-2",
            "title": "FM-2 - Fathom Pipeline Review",
        },
    ]
    assert [c["name"] for c in clickup.created_checklists] == ["Action Items", "Action Items"]
    assert [item["name"] for item in clickup.created_checklist_items] == [
        "FM-1.1 - Move Fathom actions into checklist (Deadline: 2026-05-08)",
        "FM-2.1 - Verify deployment pipeline (Deadline: 2026-05-08)",
    ]
    assert [item["orderindex"] for item in clickup.created_checklist_items] == [0, 0]
    assert clickup.created_subtasks == []


@pytest.mark.asyncio
async def test_transcript_from_webhook_is_uploaded_as_clickup_txt_attachment():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", future=True)
    session_maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.create_all)

    user_id = uuid.uuid4()
    async with session_maker() as session:
        user = User(
            id=user_id,
            email="priyanshu@theladder.ai",
            name="Priyanshu Kanyal",
            fathom_api_key_encrypted="fathom-key",
            clickup_api_token_encrypted="clickup-token",
            active=True,
        )
        session.add(user)
        await session.commit()

        payload = _payload(7003, "Transcript Attachment Review", "Attach raw transcript")
        payload["transcript"] = _transcript()
        await claim_recording(session, 7003, user_id, payload)

        clickup = RecordingClickupClient()
        await process_meeting_payload(session, user, payload, clickup=clickup)

    assert len(clickup.uploaded_attachments) == 1
    attachment = clickup.uploaded_attachments[0]
    assert attachment["clickup_token"] == "clickup-token"
    assert attachment["task_id"] == "task-1"
    assert attachment["filename"] == "fathom-transcript-7003.txt"
    assert attachment["content_type"] == "text/plain; charset=utf-8"
    assert "[00:00:03] Priyanshu Kanyal: We need to validate the transcript attachment." in attachment["content"]
    assert "[00:00:09] Priyanshu Kanyal: The attachment should be a plain text file." in attachment["content"]


@pytest.mark.asyncio
async def test_missing_webhook_transcript_is_fetched_from_fathom_and_uploaded():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", future=True)
    session_maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.create_all)

    user_id = uuid.uuid4()
    async with session_maker() as session:
        user = User(
            id=user_id,
            email="priyanshu@theladder.ai",
            name="Priyanshu Kanyal",
            fathom_api_key_encrypted="fathom-key",
            clickup_api_token_encrypted="clickup-token",
            active=True,
        )
        session.add(user)
        await session.commit()

        payload = _payload(7004, "Fetched Transcript Review", "Fetch transcript")
        await claim_recording(session, 7004, user_id, payload)

        clickup = RecordingClickupClient()
        fathom = RecordingFathomClient(_transcript())
        await process_meeting_payload(session, user, payload, clickup=clickup, fathom=fathom)
        stored_transcript = (
            await session.execute(
                text("SELECT transcript_json FROM processed_meetings WHERE recording_id = :recording_id"),
                {"recording_id": 7004},
            )
        ).scalar_one()

    assert fathom.requested_recording_ids == [("fathom-key", 7004)]
    assert "We need to validate the transcript attachment." in stored_transcript
    assert len(clickup.uploaded_attachments) == 1
    attachment = clickup.uploaded_attachments[0]
    assert attachment["filename"] == "fathom-transcript-7004.txt"
    assert "[00:00:03] Priyanshu Kanyal: We need to validate the transcript attachment." in attachment["content"]
