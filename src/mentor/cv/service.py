"""CV generation (M9, D10 to D12, D81).

1. `request` (the route) creates the next version as pending; the worker runs `generate`.
2. The generator writes the sentences (headline, summary, titles, bullets, education and
   language lines) citing profile facts, and picks the experiences and skills by ref.
3. The CV is assembled from the profile: names, companies, dates, contact details and skill
   names are copied, never written by the model. A sentence citing no fact of this profile is
   removed right away.
4. The verifier checks every remaining sentence against the facts it cites: supported, rewritten
   (the corrected text replaces it) or removed. What it changed is stored and shown (D81).

`generate` is idempotent: only a pending version is written.
"""

import logging
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from mentor.ai.bundle import AI
from mentor.ai.errors import describe
from mentor.auth.models import User
from mentor.cv import prompts
from mentor.cv.facts import Facts
from mentor.cv.models import CV, CVStatus
from mentor.cv.schemas import (
    Change,
    CheckedClaim,
    ClaimVerdict,
    Contact,
    CVContent,
    Entry,
    ExperienceEntry,
    GeneratedCV,
    Line,
    Link,
    Sourced,
    Verification,
)
from mentor.fit import years
from mentor.jobs.models import JobPosting
from mentor.profile import service as profile_service
from mentor.profile.models import Education, Experience, ExperienceBullet, Language, UserSkill

log = logging.getLogger(__name__)

DEFAULT_LANGUAGE = "pt-BR"
# The languages offered when generating (D11: the posting's by default).
LANGUAGES = ("pt-BR", "en", "es")

NO_SOURCE = "Não encontrei no seu perfil a base dessa frase."
UNCHECKED = "Não consegui conferir essa frase."


def now() -> datetime:
    return datetime.now(UTC)


def default_language(job: JobPosting) -> str:
    """The posting's language when we offer it, else Portuguese."""
    code = (job.language or "").split("-")[0].lower()
    return next((lang for lang in LANGUAGES if lang.split("-")[0] == code), DEFAULT_LANGUAGE)


async def request(db: AsyncSession, user: User, job: JobPosting, language: str) -> CV:
    """The posting's next CV version, pending (the caller commits and queues it)."""
    latest = await db.scalar(select(func.max(CV.version)).where(CV.job_id == job.id))
    cv = CV(user_id=user.id, job_id=job.id, version=(latest or 0) + 1, language=language)
    db.add(cv)
    await db.flush()
    return cv


async def job_cvs(db: AsyncSession, job: JobPosting) -> list[CV]:
    return list(
        await db.scalars(
            select(CV)
            .where(CV.job_id == job.id)
            .order_by(CV.version.desc())
            .execution_options(populate_existing=True)
        )
    )


async def generate(db: AsyncSession, ai: AI, cv_id: uuid.UUID, today: date | None = None) -> None:
    """Write a pending CV version. Never raises for a provider failure: the outcome is the
    version's status."""
    cv = await db.get(CV, cv_id, populate_existing=True)
    if cv is None or cv.status is not CVStatus.PENDING:
        return
    job = await db.scalar(
        select(JobPosting)
        .where(JobPosting.id == cv.job_id)
        .options(selectinload(JobPosting.requirements))
    )
    user = await db.get(User, cv.user_id)
    assert job is not None and user is not None
    language = cv.language
    try:
        facts = Facts(await profile_service.snapshot(db, user))
        figures = years.render(facts.snap, today or date.today())
        generated = await ai.chat.structured(GeneratedCV).ainvoke(
            prompts.generate_messages(job, facts.render(), figures, language)
        )
        draft = _assemble(user, facts, generated, language)
        if draft.claims:
            verification = await ai.chat.structured(Verification).ainvoke(
                prompts.verify_messages(draft.prompt_claims(facts), figures)
            )
            draft.apply(verification)
    except Exception as exc:
        await db.rollback()
        log.warning("CV generation failed (prompt %s): %s", prompts.VERSION, describe(exc))
        cv = await db.get(CV, cv_id, populate_existing=True)
        if cv is None:
            return
        cv.status = CVStatus.FAILED
    else:
        cv.content = draft.content.model_dump(mode="json")
        cv.changes = [c.model_dump(mode="json") for c in draft.changes]
        cv.status = CVStatus.READY
    cv.finished_at = now()
    await db.commit()


# -- assembling and verifying ------------------------------------------------------------------


@dataclass
class _Claim:
    """A sentence the model wrote, waiting for the verifier."""

    section: str
    text: str
    refs: list[str]
    replace: Callable[[str], None]  # puts a corrected text in the CV
    drop: Callable[[], None]  # takes the sentence out of the CV


@dataclass
class _Draft:
    content: CVContent
    claims: list[_Claim]
    changes: list[Change]

    def prompt_claims(self, facts: Facts) -> list[tuple[str, str, str]]:
        result = []
        for index, claim in enumerate(self.claims, start=1):
            cited = list(claim.refs)
            for ref in claim.refs:  # a bullet's experience gives it context
                fact = facts.refs[ref]
                if isinstance(fact, ExperienceBullet):
                    owner = next(e for e in facts.snap.experiences if e.id == fact.experience_id)
                    cited.append(facts.ref_of(owner))
            sources = "\n".join(f"[{r}] {facts.describe(r)}" for r in dict.fromkeys(cited))
            result.append((f"C{index}", claim.text, sources))
        return result

    def apply(self, verification: Verification) -> None:
        answers: dict[str, CheckedClaim] = {}
        for checked in verification.claims:
            answers.setdefault(checked.ref.strip().upper(), checked)
        for index, claim in enumerate(self.claims, start=1):
            answer = answers.get(f"C{index}")
            if answer is None:  # not checked: Model A keeps only what was (D12)
                claim.drop()
                self._change(claim, None, UNCHECKED)
                continue
            reason = (answer.reason or "").strip() or NO_SOURCE
            revised = (answer.revised or "").strip()
            if answer.verdict is ClaimVerdict.SUPPORTED:
                continue
            if answer.verdict is ClaimVerdict.REWRITE and revised and revised != claim.text:
                claim.replace(revised)
                self._change(claim, revised, reason)
            else:
                claim.drop()
                self._change(claim, None, reason)

    def _change(self, claim: _Claim, revised: str | None, reason: str) -> None:
        self.changes.append(
            Change(section=claim.section, original=claim.text, revised=revised, reason=reason)
        )


def _contact(user: User, facts: Facts) -> Contact:
    profile = facts.snap.profile
    location = None
    if profile is not None:
        location = ", ".join(x for x in (profile.city, profile.state) if x) or None
    return Contact(
        name=(profile.full_name if profile else None) or user.name,
        email=(profile.email if profile else None) or user.email,
        phone=profile.phone if profile else None,
        location=location,
        links=[Link(kind=link.kind, url=link.url) for link in facts.snap.links],
    )


def _month(value: date | None) -> str | None:
    return value.strftime("%Y-%m") if value else None


def _education_detail(education: Education) -> str | None:
    years = "\N{EN DASH}".join(str(y) for y in (education.start_year, education.end_year) if y)
    return " · ".join(x for x in (education.institution, years) if x) or None


def _setter(line: Line) -> Callable[[str], None]:
    def replace(text: str) -> None:
        line.text = text

    return replace


def _remover[T](items: list[T], item: T) -> Callable[[], None]:
    def drop() -> None:
        if item in items:
            items.remove(item)

    return drop


class _Assembler:
    def __init__(self, user: User, facts: Facts, language: str) -> None:
        self.facts = facts
        self.content = CVContent(language=language, contact=_contact(user, facts))
        self.draft = _Draft(self.content, claims=[], changes=[])

    def refs(self, value: Sourced, *implied: str) -> list[str]:
        cited = (r.strip().upper() for r in (*implied, *value.refs))
        return [r for r in dict.fromkeys(cited) if r in self.facts.refs]

    def line(self, section: str, value: Sourced, *implied: str) -> tuple[Line, list[str]] | None:
        """The sentence as a CV line, or None (and a change) when it cites nothing of this
        profile. The caller registers the claim, since it knows how to drop it."""
        text = value.text.strip()
        if not text:
            return None
        refs = self.refs(value, *implied)
        if not refs:
            self.draft.changes.append(
                Change(section=section, original=text, revised=None, reason=NO_SOURCE)
            )
            return None
        return Line(text=text, source_ids=[self.facts.id_of(r) for r in refs]), refs

    def claim(
        self,
        section: str,
        text: str,
        refs: list[str],
        replace: Callable[[str], None],
        drop: Callable[[], None],
    ) -> None:
        self.draft.claims.append(_Claim(section, text, refs, replace, drop))

    def build(self, generated: GeneratedCV) -> _Draft:
        content = self.content
        if (made := self.line("headline", generated.headline)) is not None:
            content.headline, refs = made
            self.claim("headline", made[0].text, refs, _setter(made[0]), self._drop_headline)
        if (made := self.line("summary", generated.summary)) is not None:
            content.summary, refs = made
            self.claim("summary", made[0].text, refs, _setter(made[0]), self._drop_summary)
        self._experiences(generated)
        for ref in dict.fromkeys(r.strip().upper() for r in generated.skill_refs):
            if isinstance(skill := self.facts.refs.get(ref), UserSkill):
                content.skills.append(Entry(fact_id=skill.id, text=skill.skill.name))
        self._items("education", generated, Education, content.education)
        self._items("language", generated, Language, content.languages)
        return self.draft

    def _drop_headline(self) -> None:
        self.content.headline = None

    def _drop_summary(self) -> None:
        self.content.summary = None

    def _experiences(self, generated: GeneratedCV) -> None:
        for gen in generated.experiences:
            ref = gen.ref.strip().upper()
            experience = self.facts.refs.get(ref)
            if not isinstance(experience, Experience) or any(
                e.fact_id == experience.id for e in self.content.experiences
            ):
                continue
            original = experience.title or ""
            entry = ExperienceEntry(
                fact_id=experience.id,
                title=Line(text=original, source_ids=[experience.id]),
                company=experience.company,
                start=_month(experience.start_date),
                end=_month(experience.end_date),
                current=experience.is_current,
            )
            self.content.experiences.append(entry)
            if (made := self.line("experience", gen.title, ref)) is not None:
                title, refs = made
                if title.text != original:  # a translated or reworded title is a claim
                    entry.title = title
                    # A title is a fact: dropping the claim puts the profile's words back.
                    self.claim(
                        "experience", title.text, refs, _setter(title), _restore(title, original)
                    )
            for bullet in gen.bullets:
                if (made := self.line("experience", bullet)) is not None:
                    line, refs = made
                    entry.bullets.append(line)
                    self.claim(
                        "experience", line.text, refs, _setter(line), _remover(entry.bullets, line)
                    )

    def _items(
        self,
        section: str,
        generated: GeneratedCV,
        kind: type[Education] | type[Language],
        target: list[Entry],
    ) -> None:
        items = generated.education if kind is Education else generated.languages
        for item in items:
            ref = item.ref.strip().upper()
            fact = self.facts.refs.get(ref)
            text = item.text.strip()
            if not isinstance(fact, kind) or not text or any(e.fact_id == fact.id for e in target):
                continue
            detail = _education_detail(fact) if isinstance(fact, Education) else None
            entry = Entry(fact_id=fact.id, text=text, detail=detail)
            target.append(entry)
            self.claim(section, text, [ref], _entry_setter(entry), _remover(target, entry))


def _restore(line: Line, original: str) -> Callable[[], None]:
    def drop() -> None:
        line.text = original

    return drop


def _entry_setter(entry: Entry) -> Callable[[str], None]:
    def replace(text: str) -> None:
        entry.text = text

    return replace


def _assemble(user: User, facts: Facts, generated: GeneratedCV, language: str) -> _Draft:
    return _Assembler(user, facts, language).build(generated)
