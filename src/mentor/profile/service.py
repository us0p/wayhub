"""Reading the profile for prompts and applying the interview's extraction patches.

The prompt shows facts with short refs (E1, F1, L1, K1, S1) instead of UUIDs; `Snapshot.refs`
maps them back, and only to the user's own facts, so a patch can never touch another user's
data.
"""

import uuid
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from urllib.parse import urlsplit

from langchain_core.embeddings import Embeddings
from sqlalchemy import inspect as sa_inspect
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from mentor.auth.models import User
from mentor.db import Base
from mentor.interview.schemas import ExperiencePatch, ProfilePatch
from mentor.profile import skills
from mentor.profile.models import (
    Education,
    Experience,
    ExperienceBullet,
    Language,
    Link,
    Profile,
    UserSkill,
)

type Fact = Experience | Education | Language | Link | UserSkill


@dataclass
class Snapshot:
    user: User
    profile: Profile | None
    experiences: list[Experience]
    education: list[Education]
    languages: list[Language]
    links: list[Link]
    skills: list[UserSkill]
    refs: dict[str, Fact] = field(default_factory=dict)

    def __post_init__(self) -> None:  # refs follow display order
        groups: list[tuple[str, list[Fact]]] = [
            ("E", list(self.experiences)),
            ("F", list(self.education)),
            ("L", list(self.languages)),
            ("K", list(self.links)),
            ("S", list(self.skills)),
        ]
        for prefix, facts in groups:
            for index, fact in enumerate(facts, start=1):
                self.refs[f"{prefix}{index}"] = fact

    def ref_of(self, fact: Fact) -> str:
        return next(ref for ref, value in self.refs.items() if value is fact)

    @property
    def is_empty(self) -> bool:
        return self.profile is None and not self.refs


async def snapshot(db: AsyncSession, user: User) -> Snapshot:
    experiences = await db.scalars(
        select(Experience)
        .where(Experience.user_id == user.id)
        .options(selectinload(Experience.bullets))
        .order_by(Experience.start_date.desc().nulls_last(), Experience.created_at)
    )
    education = await db.scalars(
        select(Education).where(Education.user_id == user.id).order_by(Education.created_at)
    )
    languages = await db.scalars(
        select(Language).where(Language.user_id == user.id).order_by(Language.created_at)
    )
    links = await db.scalars(select(Link).where(Link.user_id == user.id).order_by(Link.created_at))
    user_skills = await db.scalars(
        select(UserSkill).where(UserSkill.user_id == user.id).order_by(UserSkill.created_at)
    )
    return Snapshot(
        user=user,
        profile=await db.get(Profile, user.id),
        experiences=list(experiences),
        education=list(education),
        languages=list(languages),
        links=list(links),
        skills=list(user_skills),
    )


def stamps(count: int) -> list[datetime]:
    """`count` increasing timestamps. Rows inserted in one transaction share the database's
    `now()`, which would leave bullets in an arbitrary order; explicit stamps keep it stable."""
    start = datetime.now(UTC)
    return [start + timedelta(microseconds=i) for i in range(count)]


def _month(value: date | None) -> str:
    return value.strftime("%m/%Y") if value else "?"


def render(snap: Snapshot) -> str:
    """The profile as compact pt-BR text for prompts, with refs."""
    lines: list[str] = []
    p = snap.profile
    if p is not None:
        contact = [
            ("Nome", p.full_name),
            ("E-mail", p.email),
            ("Telefone", p.phone),
            ("Cidade", ", ".join(x for x in (p.city, p.state, p.country) if x) or None),
            ("Resumo", p.headline),
            ("Cargos-alvo", ", ".join(p.target_roles) or None),
            ("Senioridade", p.seniority.value if p.seniority else None),
            ("Modalidade", ", ".join(m.value for m in p.work_modes) or None),
            ("Locais desejados", ", ".join(p.desired_locations) or None),
            (
                "Aceita mudança",
                None
                if p.open_to_relocation is None
                else ("sim" if p.open_to_relocation else "não"),
            ),
        ]
        lines += [f"{label}: {value}" for label, value in contact if value]
    for exp in snap.experiences:
        end = "atual" if exp.is_current else _month(exp.end_date)
        extra = ", ".join(
            x
            for x in (
                exp.employment_type,
                None
                if exp.is_technical is None
                else ("técnica" if exp.is_technical else "não técnica"),
            )
            if x
        )
        lines.append(
            f"[{snap.ref_of(exp)}] Experiência: {exp.title or '?'} em {exp.company or '?'} "
            f"({_month(exp.start_date)} a {end}){f'; {extra}' if extra else ''}"
        )
        lines += [f"    - {bullet.text}" for bullet in exp.bullets]
    for edu in snap.education:
        years = f"{edu.start_year or '?'} a {edu.end_year or '?'}"
        status = edu.status.value if edu.status else "?"
        lines.append(
            f"[{snap.ref_of(edu)}] Formação: {edu.degree or '?'} em {edu.field or '?'}, "
            f"{edu.institution or '?'} ({years}; {status})"
        )
    for lang in snap.languages:
        lines.append(f"[{snap.ref_of(lang)}] Idioma: {lang.language} ({lang.level or '?'})")
    for link in snap.links:
        lines.append(f"[{snap.ref_of(link)}] Link ({link.kind}): {link.url}")
    for us in snap.skills:
        details = ", ".join(
            x
            for x in (
                f"{us.years:g} anos" if us.years is not None else None,
                us.level,
            )
            if x
        )
        lines.append(
            f"[{snap.ref_of(us)}] Habilidade: {us.skill.name}{f' ({details})' if details else ''}"
        )
    return "\n".join(lines) or "(perfil ainda vazio)"


def parse_month(value: str | None) -> date | None:
    """'2021-03' or '2021' → first day of that month (January for a bare year)."""
    if not value:
        return None
    parts = value.strip().split("-")
    try:
        year = int(parts[0])
        month = int(parts[1]) if len(parts) > 1 else 1
        return date(year, month, 1) if 1950 <= year <= 2100 else None
    except (ValueError, IndexError):
        return None


def safe_url(url: str) -> str | None:
    """Only absolute http(s) URLs are stored; anything else (javascript:, data:) is dropped."""
    url = url.strip()
    if "://" not in url and "." in url.split("/")[0]:
        url = "https://" + url
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.netloc or len(url) > 500:
        return None
    return url


def _fit(model: type[Base], name: str, value: object) -> object:
    """Clip strings (and string list items) to their column's length, so one over-long value
    from the model doesn't fail the whole patch."""
    column_type = sa_inspect(model).columns[name].type
    if isinstance(value, str) and (length := getattr(column_type, "length", None)):
        return value[:length]
    if (
        isinstance(value, list)
        and isinstance(column_type, ARRAY)
        and (length := getattr(column_type.item_type, "length", None))
    ):
        return [v[:length] if isinstance(v, str) else v for v in value]
    return value


def _set(obj: Base, **values: object) -> None:
    """Assign the values that are not None (None in a patch means "unchanged"), clipped to
    their column sizes."""
    for name, value in values.items():
        if value is not None:
            setattr(obj, name, _fit(type(obj), name, value))


async def apply_patch(
    db: AsyncSession,
    embeddings: Embeddings,
    snap: Snapshot,
    patch: ProfilePatch,
    turn_id: uuid.UUID,
) -> None:
    """Apply an extraction patch to the user's profile. Unknown refs are ignored."""
    user_id = snap.user.id

    removed: set[Fact] = {snap.refs[r] for r in patch.remove_refs if r in snap.refs}
    for fact in removed:
        await db.delete(fact)
    removed_ids = {fact.id for fact in removed if isinstance(fact, Experience)}
    if removed_ids:
        for us in snap.skills:
            us.experience_ids = [i for i in us.experience_ids if i not in removed_ids]

    if patch.contact or patch.preferences:
        profile = snap.profile
        if profile is None:
            profile = Profile(user_id=user_id)
            db.add(profile)
            snap.profile = profile
        if patch.contact:
            _set(profile, **patch.contact.model_dump())
        if patch.preferences:
            _set(profile, **patch.preferences.model_dump())
        profile.source_turn_id = turn_id

    exp_by_ref: dict[str, Experience] = {}
    new_bullets: list[ExperienceBullet] = []
    for ep in patch.experiences:
        experience = await _upsert_experience(db, snap, ep, turn_id, removed)
        if experience is None:
            continue
        if ep.ref:
            exp_by_ref[ep.ref] = experience
        texts = [t.strip() for t in ep.new_bullets if t.strip()]
        for text, created in zip(texts, stamps(len(texts)), strict=True):
            bullet = ExperienceBullet(
                user_id=user_id,
                experience_id=experience.id,
                text=text,
                source_turn_id=turn_id,
                created_at=created,
            )
            db.add(bullet)
            new_bullets.append(bullet)
    if new_bullets:
        vectors = await embeddings.aembed_documents([b.text for b in new_bullets])
        for bullet, vector in zip(new_bullets, vectors, strict=True):
            bullet.embedding = vector

    for edu in patch.education:
        existing = snap.refs.get(edu.ref) if edu.ref else None
        values = edu.model_dump(exclude={"ref"})
        if isinstance(existing, Education) and existing not in removed:
            _set(existing, **values)
            existing.source_turn_id = turn_id
        elif edu.institution or edu.degree or edu.field:
            education = Education(user_id=user_id, source_turn_id=turn_id)
            _set(education, **values)
            db.add(education)

    known_languages = {
        lang.language.lower(): lang for lang in snap.languages if lang not in removed
    }
    for lp in patch.languages:
        name = lp.language.strip()
        if not name:
            continue
        if (language := known_languages.get(name.lower())) is not None:
            _set(language, level=lp.level)
            language.source_turn_id = turn_id
        else:
            language = Language(user_id=user_id, source_turn_id=turn_id)
            _set(language, language=name, level=lp.level)
            db.add(language)
            known_languages[name.lower()] = language

    known_urls = {link.url for link in snap.links if link not in removed}
    for lk in patch.links:
        url = safe_url(lk.url)
        if url and url not in known_urls:
            db.add(Link(user_id=user_id, kind=lk.kind[:40], url=url, source_turn_id=turn_id))
            known_urls.add(url)

    await db.flush()  # new experiences need ids before skills reference them

    known_skills = {us.skill_id: us for us in snap.skills if us not in removed}
    for sp in patch.skills:
        if not sp.name.strip():
            continue
        skill = await skills.normalize(db, embeddings, sp.name)
        experience_ids = [
            exp.id
            for ref in sp.experience_refs
            if isinstance(exp := exp_by_ref.get(ref) or snap.refs.get(ref), Experience)
        ]
        user_skill = known_skills.get(skill.id)
        if user_skill is None:
            user_skill = UserSkill(
                user_id=user_id,
                skill_id=skill.id,
                raw_name=sp.name.strip()[:120],
                experience_ids=[],
            )
            db.add(user_skill)
            known_skills[skill.id] = user_skill
        _set(user_skill, years=sp.years, level=sp.level)
        user_skill.experience_ids = list(
            dict.fromkeys([*user_skill.experience_ids, *experience_ids])
        )
        user_skill.source_turn_id = turn_id

    await db.flush()


async def _upsert_experience(
    db: AsyncSession,
    snap: Snapshot,
    ep: ExperiencePatch,
    turn_id: uuid.UUID,
    removed: set[Fact],
) -> Experience | None:
    values = {
        "company": ep.company,
        "title": ep.title,
        "start_date": parse_month(ep.start),
        "end_date": parse_month(ep.end),
        "is_current": ep.is_current,
        "employment_type": ep.employment_type,
        "is_technical": ep.is_technical,
    }
    existing = snap.refs.get(ep.ref) if ep.ref else None
    if isinstance(existing, Experience) and existing not in removed:
        _set(existing, **values)
        existing.source_turn_id = turn_id
        return existing
    if not (ep.company or ep.title):
        return None
    experience = Experience(user_id=snap.user.id, source_turn_id=turn_id)
    _set(experience, **values)
    db.add(experience)
    await db.flush()
    return experience
