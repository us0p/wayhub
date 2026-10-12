"""The profile as citable facts for CV generation (M9, D81). Every fact gets a short ref the
model cites: E (experiences), B (their bullets), F (education), L (languages), S (skills) and
P1 (the profile's own statements: headline, target roles, seniority). Links and contact details
are copied into the CV as they are, so they are not cited."""

import uuid
from dataclasses import dataclass, field
from datetime import date

from mentor.profile.models import (
    Education,
    Experience,
    ExperienceBullet,
    Language,
    Profile,
    UserSkill,
)
from mentor.profile.service import Snapshot

type Fact = Profile | Experience | ExperienceBullet | Education | Language | UserSkill


def _month(value: date | None) -> str:
    return value.strftime("%m/%Y") if value else "?"


@dataclass
class Facts:
    snap: Snapshot
    refs: dict[str, Fact] = field(default_factory=dict)

    def __post_init__(self) -> None:
        snap = self.snap
        if snap.profile is not None and (
            snap.profile.headline or snap.profile.target_roles or snap.profile.seniority
        ):
            self.refs["P1"] = snap.profile
        bullets = 0
        for ref, fact in snap.refs.items():
            if isinstance(fact, Experience | Education | Language | UserSkill):
                self.refs[ref] = fact
            if isinstance(fact, Experience):
                for bullet in fact.bullets:
                    bullets += 1
                    self.refs[f"B{bullets}"] = bullet

    def id_of(self, ref: str) -> uuid.UUID:
        """The fact's id, stored with the CV lines it backs (the profile's is the user's)."""
        fact = self.refs[ref]
        return fact.user_id if isinstance(fact, Profile) else fact.id

    def ref_of(self, fact: Fact) -> str:
        return next(ref for ref, value in self.refs.items() if value is fact)

    def describe(self, ref: str) -> str:
        """The fact as text, for the generator and the verifier."""
        fact = self.refs[ref]
        match fact:
            case Profile():
                parts = [
                    f"resumo: {fact.headline}" if fact.headline else None,
                    f"cargos-alvo: {', '.join(fact.target_roles)}" if fact.target_roles else None,
                    f"senioridade: {fact.seniority.value}" if fact.seniority else None,
                ]
                return "Perfil (" + "; ".join(p for p in parts if p) + ")"
            case Experience():
                end = "atual" if fact.is_current else _month(fact.end_date)
                kind = (
                    ""
                    if fact.is_technical is None
                    else ("; técnica" if fact.is_technical else "; não técnica")
                )
                return (
                    f"Experiência: {fact.title or '?'} em {fact.company or '?'} "
                    f"({_month(fact.start_date)} a {end}{kind})"
                )
            case ExperienceBullet():
                owner = next(e for e in self.snap.experiences if e.id == fact.experience_id)
                return f"Sobre {self.ref_of(owner)}: {fact.text}"
            case Education():
                years = f"{fact.start_year or '?'} a {fact.end_year or '?'}"
                status = fact.status.value if fact.status else "?"
                return (
                    f"Formação: {fact.degree or '?'} em {fact.field or '?'}, "
                    f"{fact.institution or '?'} ({years}; {status})"
                )
            case Language():
                return f"Idioma: {fact.language} ({fact.level or '?'})"
            case UserSkill():
                details = [
                    f"{fact.years:g} anos" if fact.years is not None else None,
                    fact.level,
                ]
                used = [
                    self.ref_of(e) for e in self.snap.experiences if e.id in fact.experience_ids
                ]
                text = f"Habilidade: {fact.skill.name}"
                if any(details):
                    text += f" ({', '.join(d for d in details if d)})"
                return text + (f"; usada em {', '.join(used)}" if used else "")
        raise AssertionError(ref)

    def render(self) -> str:
        """Every fact with its ref, experiences followed by their bullets."""
        lines = []
        for ref, fact in self.refs.items():
            indent = "    " if isinstance(fact, ExperienceBullet) else ""
            lines.append(f"{indent}[{ref}] {self.describe(ref)}")
        return "\n".join(lines)
