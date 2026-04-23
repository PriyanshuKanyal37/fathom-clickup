import asyncio
import uuid

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlmodel import SQLModel

from app.models import ProcessedMeeting, User
from app.services import claim_recording


@pytest.mark.asyncio
async def test_claim_recording_allows_only_one():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", future=True)
    session_maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.create_all)

    user_id = uuid.uuid4()
    async with session_maker() as session:
        session.add(
            User(
                id=user_id,
                email="a@b.com",
                name="A",
                fathom_api_key_encrypted="enc1",
                clickup_api_token_encrypted="enc2",
                active=True,
            )
        )
        await session.commit()

    async def try_claim():
        async with session_maker() as session:
            return await claim_recording(session, 9991, user_id, {"recording_id": 9991})

    results = await asyncio.gather(*[try_claim() for _ in range(5)])
    assert sum(1 for r in results if r) == 1

    async with session_maker() as session:
        rows = await session.get(ProcessedMeeting, 9991)
        assert rows is not None

