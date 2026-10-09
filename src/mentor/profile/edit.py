"""Editing the profile by hand (D15, D60): parse and validate a submitted form, then save it.

Facts edited here are written by the candidate themselves, so `source_turn_id` is cleared:
no interview turn is their source any more (Model A treats them as stated by the user).
Parsers return `(values, errors)`; errors map a field name to a pt-BR message and a profile is
only written when there are none. The same dictionaries (`form_values`) feed the form templates,
so a rejected form is shown again exactly as typed.
"""

import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from enum import StrEnum
from typing import Any

from langchain_core.embeddings import Embeddings
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.datastructures import FormData

from mentor.auth.models import User
from mentor.i18n import gettext_ as _
from mentor.profile import skills
from mentor.profile.models import (
    Education,
    EducationStatus,
    Experience,
    ExperienceBullet,
    Language,
    Link,
    Profile,
    Seniority,
    UserSkill,
    WorkMode,
)
from mentor.profile.service import Fact, parse_month, safe_url, stamps

type Values = dict[str, Any]
type Errors = dict[str, str]

MAX_LIST_ITEMS = 20
MAX_BULLETS = 30
MAX_BULLET_LENGTH = 1000
LINK_KINDS = ("linkedin", "github", "portfolio", "other")


class Reader:
    """Reads one submitted form and collects the errors of the fields it rejects."""

    def __init__(self, form: FormData) -> None:
        self.form = form
        self.errors: Errors = {}

    def _fail(self, name: str, message: str) -> None:
        self.errors.setdefault(name, message)

    def text(self, name: str, max_length: int, *, required: bool = False) -> str | None:
        value = " ".join(str(self.form.get(name, "")).split())
        if not value:
            if required:
                self._fail(name, _("Preencha este campo."))
            return None
        if len(value) > max_length:
            self._fail(name, _("Use no máximo %(n)s caracteres.") % {"n": max_length})
        return value

    def choice[E: StrEnum](self, name: str, enum: type[E]) -> E | None:
        value = str(self.form.get(name, "")).strip()
        if not value:
            return None
        try:
            return enum(value)
        except ValueError:
            self._fail(name, _("Escolha uma das opções."))
            return None

    def choices[E: StrEnum](self, name: str, enum: type[E]) -> list[E]:
        picked: list[E] = []
        for value in self.form.getlist(name):
            try:
                member = enum(str(value))
            except ValueError:
                self._fail(name, _("Escolha uma das opções."))
                continue
            if member not in picked:
                picked.append(member)
        return picked

    def flag(self, name: str) -> bool:
        return str(self.form.get(name, "")) == "sim"

    def yes_no(self, name: str) -> bool | None:
        match str(self.form.get(name, "")):
            case "sim":
                return True
            case "nao":
                return False
            case "":
                return None
            case _:
                self._fail(name, _("Escolha uma das opções."))
                return None

    def month(self, name: str) -> date | None:
        raw = str(self.form.get(name, "")).strip()
        if not raw:
            return None
        if not re.fullmatch(r"\d{4}-\d{2}", raw) or (value := parse_month(raw)) is None:
            self._fail(name, _("Informe mês e ano válidos."))
            return None
        return value

    def year(self, name: str) -> int | None:
        raw = str(self.form.get(name, "")).strip()
        if not raw:
            return None
        if not raw.isdigit() or not 1950 <= int(raw) <= 2100:
            self._fail(name, _("Informe um ano entre 1950 e 2100."))
            return None
        return int(raw)

    def number(self, name: str, *, maximum: float) -> float | None:
        raw = str(self.form.get(name, "")).strip().replace(",", ".")
        if not raw:
            return None
        try:
            value = float(raw)
        except ValueError:
            self._fail(name, _("Informe um número."))
            return None
        if not 0 <= value <= maximum:
            self._fail(name, _("Informe um valor entre 0 e %(n)s.") % {"n": f"{maximum:g}"})
            return None
        return value

    def items(self, name: str, max_length: int, *, separator: str = ",") -> list[str]:
        """A comma- or line-separated list, without blanks or repeats."""
        raw = str(self.form.get(name, "")).replace("\r", "")
        parts = [" ".join(p.split()) for p in re.split(rf"[{re.escape(separator)}\n]", raw)]
        unique = list(dict.fromkeys(p for p in parts if p))
        if len(unique) > MAX_LIST_ITEMS or any(len(p) > max_length for p in unique):
            self._fail(name, _("Use até %(n)s itens curtos.") % {"n": MAX_LIST_ITEMS})
        return unique[:MAX_LIST_ITEMS]


def raw_values(form: FormData) -> Values:
    """The submitted fields as typed, for showing a rejected form again."""
    values: Values = {key: str(value) for key, value in form.items()}
    values["work_modes"] = [str(v) for v in form.getlist("work_modes")]
    return values


# --- contact and preferences ---------------------------------------------------------------


def parse_contact(form: FormData) -> tuple[Values, Errors]:
    r = Reader(form)
    email = r.text("email", 320)
    if email and not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email):
        r.errors["email"] = _("Informe um e-mail válido.")
    values: Values = {
        "full_name": r.text("full_name", 200),
        "email": email,
        "phone": r.text("phone", 40),
        "city": r.text("city", 120),
        "state": r.text("state", 120),
        "country": r.text("country", 120),
        "headline": r.text("headline", 300),
        "seniority": r.choice("seniority", Seniority),
        "work_modes": r.choices("work_modes", WorkMode),
        "desired_locations": r.items("desired_locations", 120),
        "open_to_relocation": r.yes_no("open_to_relocation"),
        "target_roles": r.items("target_roles", 120),
    }
    return values, r.errors


def contact_form_values(profile: Profile | None) -> Values:
    if profile is None:
        return {"work_modes": []}
    return {
        "full_name": profile.full_name or "",
        "email": profile.email or "",
        "phone": profile.phone or "",
        "city": profile.city or "",
        "state": profile.state or "",
        "country": profile.country or "",
        "headline": profile.headline or "",
        "seniority": profile.seniority.value if profile.seniority else "",
        "work_modes": [m.value for m in profile.work_modes],
        "desired_locations": ", ".join(profile.desired_locations),
        "open_to_relocation": _yes_no(profile.open_to_relocation),
        "target_roles": ", ".join(profile.target_roles),
    }


def _yes_no(value: bool | None) -> str:
    return "" if value is None else ("sim" if value else "nao")


async def save_contact(db: AsyncSession, user: User, values: Values) -> Profile:
    profile = await db.get(Profile, user.id)
    if profile is None:
        profile = Profile(user_id=user.id)
        db.add(profile)
    for name, value in values.items():
        setattr(profile, name, value)
    profile.source_turn_id = None
    await db.flush()
    await db.refresh(profile)  # onupdate columns
    return profile


# --- experiences ---------------------------------------------------------------------------


def parse_experience(form: FormData) -> tuple[Values, Errors]:
    r = Reader(form)
    company = r.text("company", 200)
    title = r.text("title", 200)
    if not (company or title):
        r.errors["company"] = _("Informe a empresa ou o cargo.")
    start, end = r.month("start_date"), r.month("end_date")
    is_current = r.flag("is_current")
    if is_current:
        end = None
    elif start and end and end < start:
        r.errors["end_date"] = _("O fim não pode ser antes do início.")
    bullets = [
        " ".join(line.split())
        for line in str(form.get("bullets", "")).replace("\r", "").split("\n")
    ]
    bullets = [b for b in bullets if b]
    if len(bullets) > MAX_BULLETS or any(len(b) > MAX_BULLET_LENGTH for b in bullets):
        r.errors["bullets"] = _("Use até %(n)s linhas de até %(m)s caracteres.") % {
            "n": MAX_BULLETS,
            "m": MAX_BULLET_LENGTH,
        }
    values: Values = {
        "company": company,
        "title": title,
        "start_date": start,
        "end_date": end,
        "is_current": is_current,
        "employment_type": r.text("employment_type", 60),
        "is_technical": r.yes_no("is_technical"),
        "bullets": bullets,
    }
    return values, r.errors


def experience_form_values(experience: Experience | None) -> Values:
    if experience is None:
        return {}
    return {
        "company": experience.company or "",
        "title": experience.title or "",
        "start_date": experience.start_date.strftime("%Y-%m") if experience.start_date else "",
        "end_date": experience.end_date.strftime("%Y-%m") if experience.end_date else "",
        "is_current": "sim" if experience.is_current else "",
        "employment_type": experience.employment_type or "",
        "is_technical": _yes_no(experience.is_technical),
        "bullets": "\n".join(b.text for b in experience.bullets),
    }


async def save_experience(
    db: AsyncSession,
    embeddings: Embeddings,
    user: User,
    experience: Experience | None,
    values: Values,
) -> Experience:
    """Create or update an experience. Its bullets follow the textarea line by line: a line
    keeps the bullet at the same position (re-embedded if the text changed), extra lines are new
    bullets, missing lines delete the last ones."""
    values = dict(values)
    lines: list[str] = values.pop("bullets")
    if experience is None:
        experience = Experience(user_id=user.id)
        db.add(experience)
        await db.flush()
        existing: list[ExperienceBullet] = []
    else:
        existing = list(experience.bullets)
    for name, value in values.items():
        setattr(experience, name, value)
    experience.source_turn_id = None

    changed: list[ExperienceBullet] = []
    for bullet, line in zip(existing, lines, strict=False):
        if bullet.text != line:
            bullet.text = line
            bullet.source_turn_id = None
            changed.append(bullet)
    for bullet in existing[len(lines) :]:
        await db.delete(bullet)
    added = lines[len(existing) :]
    new = [
        ExperienceBullet(user_id=user.id, experience_id=experience.id, text=text, created_at=at)
        for text, at in zip(added, stamps(len(added)), strict=True)
    ]
    db.add_all(new)
    if changed or new:
        vectors = await embeddings.aembed_documents([b.text for b in [*changed, *new]])
        for bullet, vector in zip([*changed, *new], vectors, strict=True):
            bullet.embedding = vector
    await db.flush()
    await db.refresh(experience, ["bullets"])
    return experience


# --- education -----------------------------------------------------------------------------


def parse_education(form: FormData) -> tuple[Values, Errors]:
    r = Reader(form)
    values: Values = {
        "institution": r.text("institution", 200),
        "degree": r.text("degree", 200),
        "field": r.text("field", 200),
        "start_year": r.year("start_year"),
        "end_year": r.year("end_year"),
        "status": r.choice("status", EducationStatus),
    }
    if not (values["institution"] or values["degree"] or values["field"]):
        r.errors["institution"] = _("Informe a instituição, o curso ou a área.")
    start, end = values["start_year"], values["end_year"]
    if start and end and end < start:
        r.errors["end_year"] = _("O fim não pode ser antes do início.")
    return values, r.errors


def education_form_values(education: Education | None) -> Values:
    if education is None:
        return {}
    return {
        "institution": education.institution or "",
        "degree": education.degree or "",
        "field": education.field or "",
        "start_year": education.start_year or "",
        "end_year": education.end_year or "",
        "status": education.status.value if education.status else "",
    }


# --- languages -----------------------------------------------------------------------------


def parse_language(form: FormData) -> tuple[Values, Errors]:
    r = Reader(form)
    values: Values = {
        "language": r.text("language", 80, required=True),
        "level": r.text("level", 80),
    }
    return values, r.errors


def language_form_values(language: Language | None) -> Values:
    return (
        {} if language is None else {"language": language.language, "level": language.level or ""}
    )


# --- links ---------------------------------------------------------------------------------


def parse_link(form: FormData) -> tuple[Values, Errors]:
    r = Reader(form)
    raw = r.text("url", 500, required=True)
    url = safe_url(raw) if raw else None
    if raw and url is None and "url" not in r.errors:
        r.errors["url"] = _("Informe um endereço http ou https válido.")
    kind = str(form.get("kind", "")).strip() or "other"
    if kind not in LINK_KINDS:
        r.errors["kind"] = _("Escolha uma das opções.")
    return {"kind": kind, "url": url}, r.errors


def link_form_values(link: Link | None) -> Values:
    return {} if link is None else {"kind": link.kind, "url": link.url}


# --- skills --------------------------------------------------------------------------------


def parse_skill(form: FormData) -> tuple[Values, Errors]:
    r = Reader(form)
    values: Values = {
        "name": r.text("name", 120, required=True),
        "years": r.number("years", maximum=60),
        "level": r.text("level", 60),
    }
    return values, r.errors


def skill_form_values(user_skill: UserSkill | None) -> Values:
    if user_skill is None:
        return {}
    return {
        "name": user_skill.skill.name,
        "years": "" if user_skill.years is None else f"{user_skill.years:g}",
        "level": user_skill.level or "",
    }


# --- saving the simple kinds ---------------------------------------------------------------


class Conflict(Exception):
    """The edit would duplicate another of the user's facts; carries the field and message."""

    def __init__(self, field: str, message: str) -> None:
        super().__init__(field)
        self.field, self.message = field, message


async def save_education(
    db: AsyncSession,
    embeddings: Embeddings,
    user: User,
    education: Education | None,
    values: Values,
) -> Education:
    if education is None:
        education = Education(user_id=user.id)
        db.add(education)
    for name, value in values.items():
        setattr(education, name, value)
    education.source_turn_id = None
    await db.flush()
    return education


async def save_language(
    db: AsyncSession, embeddings: Embeddings, user: User, language: Language | None, values: Values
) -> Language:
    others = await db.scalars(select(Language).where(Language.user_id == user.id))
    wanted = values["language"].lower()
    if any(o.language.lower() == wanted and o is not language for o in others):
        raise Conflict("language", _("Esse idioma já está na lista."))
    if language is None:
        language = Language(user_id=user.id)
        db.add(language)
    for name, value in values.items():
        setattr(language, name, value)
    language.source_turn_id = None
    await db.flush()
    return language


async def save_link(
    db: AsyncSession, embeddings: Embeddings, user: User, link: Link | None, values: Values
) -> Link:
    others = await db.scalars(select(Link).where(Link.user_id == user.id))
    if any(o.url == values["url"] and o is not link for o in others):
        raise Conflict("url", _("Esse link já está na lista."))
    if link is None:
        link = Link(user_id=user.id)
        db.add(link)
    for name, value in values.items():
        setattr(link, name, value)
    link.source_turn_id = None
    await db.flush()
    return link


async def save_skill(
    db: AsyncSession,
    embeddings: Embeddings,
    user: User,
    user_skill: UserSkill | None,
    values: Values,
) -> UserSkill:
    skill = await skills.normalize(db, embeddings, values["name"])
    others = await db.scalars(select(UserSkill).where(UserSkill.user_id == user.id))
    if any(o.skill_id == skill.id and o is not user_skill for o in others):
        raise Conflict("name", _("Essa habilidade já está na lista."))
    if user_skill is None:
        user_skill = UserSkill(user_id=user.id, experience_ids=[])
        db.add(user_skill)
    user_skill.skill_id = skill.id
    user_skill.skill = skill
    user_skill.raw_name = values["name"][:120]
    user_skill.years = values["years"]
    user_skill.level = values["level"]
    user_skill.source_turn_id = None
    await db.flush()
    return user_skill


async def delete_fact(db: AsyncSession, user: User, fact: Fact) -> None:
    if isinstance(fact, Experience):
        for user_skill in await db.scalars(select(UserSkill).where(UserSkill.user_id == user.id)):
            if fact.id in user_skill.experience_ids:
                user_skill.experience_ids = [i for i in user_skill.experience_ids if i != fact.id]
    await db.delete(fact)
    await db.flush()


# --- registry ------------------------------------------------------------------------------


@dataclass(frozen=True)
class Kind:
    """One editable kind of fact: how to parse, show and save it. `slug` is the URL segment and
    `template` the file under `profile/items/`."""

    slug: str
    template: str
    model: type[Fact]
    parse: Callable[[FormData], tuple[Values, Errors]]
    form_values: Callable[[Any], Values]
    save: Callable[..., Any]


KINDS: dict[str, Kind] = {
    kind.slug: kind
    for kind in (
        Kind(
            "experiencias",
            "experience",
            Experience,
            parse_experience,
            experience_form_values,
            save_experience,
        ),
        Kind(
            "formacoes",
            "education",
            Education,
            parse_education,
            education_form_values,
            save_education,
        ),
        Kind("idiomas", "language", Language, parse_language, language_form_values, save_language),
        Kind("links", "link", Link, parse_link, link_form_values, save_link),
        Kind("habilidades", "skill", UserSkill, parse_skill, skill_form_values, save_skill),
    )
}
