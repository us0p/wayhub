"""Skill normalization (D19): map what the candidate said to a shared canonical skill.

1. Exact match on the name or an alias (case-insensitive).
2. Otherwise the nearest skill by embedding, if it is very close: the raw name becomes an alias.
3. Otherwise a new `uncategorized` skill, to be curated later (SCOPE.md, Phase 1.5).

The threshold is deliberately strict: merging two different skills (Java/JavaScript) would put
a skill the candidate never claimed into their CV, which Model A forbids (D12). A missed merge
only costs a duplicate skill.
"""

from langchain_core.embeddings import Embeddings
from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from mentor.profile.models import Skill, SkillStatus

MIN_SIMILARITY = 0.92


def _clean(name: str) -> str:
    return " ".join(name.split())[:120]


async def _find(db: AsyncSession, key: str) -> Skill | None:
    return await db.scalar(
        select(Skill)
        .where(or_(func.lower(Skill.name) == key, Skill.aliases.contains([key])))
        .limit(1)
    )


async def normalize(db: AsyncSession, embeddings: Embeddings, raw_name: str) -> Skill:
    name = _clean(raw_name)
    key = name.lower()
    if (skill := await _find(db, key)) is not None:
        return skill

    # Skills are embedded like documents on both sides (stored and new), so the comparison
    # is symmetric.
    [vector] = await embeddings.aembed_documents([name])
    distance = Skill.embedding.cosine_distance(vector)
    row = (
        await db.execute(
            select(Skill, distance.label("distance"))
            .where(Skill.embedding.is_not(None))
            .order_by(distance)
            .limit(1)
        )
    ).first()
    if row is not None and 1 - row.distance >= MIN_SIMILARITY:
        nearest: Skill = row.Skill
        nearest.aliases = [*nearest.aliases, key]
        return nearest

    skill = Skill(name=name, aliases=[], embedding=vector, status=SkillStatus.UNCATEGORIZED)
    try:
        async with db.begin_nested():  # another user may create the same skill concurrently
            db.add(skill)
    except IntegrityError:
        existing = await _find(db, key)
        assert existing is not None
        return existing
    return skill
