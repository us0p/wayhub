"""Database helpers shared by the interview engine, its agent graph and other pages."""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from mentor.auth.models import User
from mentor.interview.checklist import ChecklistItem, ItemState, Progress
from mentor.interview.models import ChecklistEntry, ChecklistStatus, Interview
from mentor.interview.schemas import ProfilePatch


async def latest_interview(db: AsyncSession, user: User) -> Interview | None:
    return await db.scalar(
        select(Interview)
        .where(Interview.user_id == user.id)
        .order_by(Interview.seq.desc())
        .limit(1)
    )


async def checklist_progress(db: AsyncSession, user: User) -> Progress:
    entries = {
        e.item: e
        for e in await db.scalars(select(ChecklistEntry).where(ChecklistEntry.user_id == user.id))
    }
    return Progress(
        tuple(
            ItemState(item, entries[item].status, entries[item].note)
            if item in entries
            else ItemState(item, ChecklistStatus.MISSING)
            for item in ChecklistItem
        )
    )


async def update_checklist(db: AsyncSession, user: User, patch: ProfilePatch) -> None:
    for change in patch.checklist:
        entry = await db.get(ChecklistEntry, (user.id, change.item.value))
        if entry is None:
            entry = ChecklistEntry(user_id=user.id, item=change.item.value)
            db.add(entry)
        entry.status = change.status
        entry.note = None if change.status is ChecklistStatus.DONE else change.note
