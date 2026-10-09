import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from mentor.ai.adapters.fake import FakeEmbeddings
from mentor.profile import skills
from mentor.profile.models import Skill, SkillStatus

# `skills` is shared across users: unique names keep tests independent of other data.


def _unique(name: str) -> str:
    return f"{name} {uuid.uuid4().hex[:6]}"


async def test_unknown_skill_is_created_uncategorized_with_an_embedding(db: AsyncSession) -> None:
    name = _unique("Elixir")

    skill = await skills.normalize(db, FakeEmbeddings(), f"  {name} ")

    assert skill.name == name
    assert skill.status is SkillStatus.UNCATEGORIZED
    assert skill.embedding is not None


async def test_same_name_or_alias_resolves_case_insensitively(db: AsyncSession) -> None:
    embedder = FakeEmbeddings()
    name = _unique("TypeScript")
    skill = await skills.normalize(db, embedder, name)
    skill.aliases = [f"ts-{name[-6:]}"]
    await db.flush()

    assert await skills.normalize(db, embedder, name.upper()) is skill
    assert await skills.normalize(db, embedder, f"TS-{name[-6:]}") is skill
    assert len(embedder.calls) == 1  # exact matches never call the embedder


async def test_a_near_identical_spelling_becomes_an_alias(db: AsyncSession) -> None:
    embedder = FakeEmbeddings()
    suffix = uuid.uuid4().hex[:6]
    skill = await skills.normalize(db, embedder, f"Node.js {suffix}")

    same = await skills.normalize(db, embedder, f"node js {suffix}")  # same words for the fake

    assert same is skill
    assert f"node js {suffix}" in skill.aliases


async def test_different_skills_are_not_merged(db: AsyncSession) -> None:
    embedder = FakeEmbeddings()
    suffix = uuid.uuid4().hex[:6]

    java = await skills.normalize(db, embedder, f"Java {suffix}")
    javascript = await skills.normalize(db, embedder, f"JavaScript {suffix}")

    assert java is not javascript
    assert isinstance(javascript, Skill)
