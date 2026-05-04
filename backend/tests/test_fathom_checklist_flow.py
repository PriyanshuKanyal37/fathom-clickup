import uuid

import pytest
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
        first_task_id = await process_meeting_payload(session, user, first_payload, clickup=clickup)
        second_task_id = await process_meeting_payload(session, user, second_payload, clickup=clickup)

    assert first_task_id == "task-1"
    assert second_task_id == "task-2"
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
