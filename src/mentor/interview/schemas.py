"""Structured output of the extraction call: a patch to the profile plus checklist updates.

Kept flat and simple (optional scalars, lists of small objects) so it maps cleanly to the
provider's JSON-schema support. `ref` values are the short ids the prompt showed (E1, F1,
S1...); a null ref means a new fact.
"""

from pydantic import BaseModel, Field

from mentor.interview.checklist import ChecklistItem
from mentor.interview.models import ChecklistStatus
from mentor.profile.models import EducationStatus, Seniority, WorkMode


class ContactPatch(BaseModel):
    full_name: str | None = None
    email: str | None = None
    phone: str | None = None
    city: str | None = None
    state: str | None = None
    country: str | None = None
    headline: str | None = Field(None, description="Resumo profissional em uma linha")


class PreferencesPatch(BaseModel):
    work_modes: list[WorkMode] | None = None
    desired_locations: list[str] | None = None
    open_to_relocation: bool | None = None
    seniority: Seniority | None = None
    target_roles: list[str] | None = None


class ExperiencePatch(BaseModel):
    ref: str | None = Field(None, description="E-ref of an existing experience; null = new")
    company: str | None = None
    title: str | None = None
    start: str | None = Field(None, description="YYYY-MM or YYYY")
    end: str | None = Field(None, description="YYYY-MM or YYYY; null if current or unknown")
    is_current: bool | None = None
    employment_type: str | None = Field(None, description="CLT, PJ, estágio, freelance...")
    is_technical: bool | None = None
    new_bullets: list[str] = Field(
        default_factory=list,
        description="New facts about this experience, each one short, in the user's terms",
    )


class EducationPatch(BaseModel):
    ref: str | None = Field(None, description="F-ref of an existing entry; null = new")
    institution: str | None = None
    degree: str | None = None
    field: str | None = None
    start_year: int | None = None
    end_year: int | None = None
    status: EducationStatus | None = None


class LanguagePatch(BaseModel):
    language: str
    level: str | None = None


class LinkPatch(BaseModel):
    kind: str = Field(description="linkedin, github, portfolio or other")
    url: str


class SkillPatch(BaseModel):
    name: str = Field(description="Canonical, commonly used name (e.g. JavaScript, not JS)")
    years: float | None = None
    level: str | None = None
    experience_refs: list[str] = Field(default_factory=list)


class ChecklistUpdate(BaseModel):
    item: ChecklistItem
    status: ChecklistStatus
    note: str | None = Field(None, description="What is still missing, in pt-BR")


class ProfilePatch(BaseModel):
    contact: ContactPatch | None = None
    preferences: PreferencesPatch | None = None
    experiences: list[ExperiencePatch] = Field(default_factory=list)
    education: list[EducationPatch] = Field(default_factory=list)
    languages: list[LanguagePatch] = Field(default_factory=list)
    links: list[LinkPatch] = Field(default_factory=list)
    skills: list[SkillPatch] = Field(default_factory=list)
    remove_refs: list[str] = Field(
        default_factory=list, description="Refs the user said are wrong and must be removed"
    )
    checklist: list[ChecklistUpdate] = Field(default_factory=list)


class TurnPlan(BaseModel):
    """The planner's decision for the next interviewer message (D54). `reasoning` is private:
    it is never shown to the user nor passed to the speaker."""

    reasoning: str = Field(
        description="Notas privadas: o que já se sabe, o que falta e por que escolher o próximo "
        "passo. Nunca é mostrado à pessoa."
    )
    focus: ChecklistItem | None = Field(
        None, description="Item do checklist que a próxima pergunta cobre (null se nenhum)"
    )
    brief: str = Field(
        description="Instrução curta para quem vai escrever a mensagem: o que reconhecer da "
        "última resposta e qual única pergunta fazer (ou como encerrar, se done)."
    )
    done: bool = Field(
        False,
        description="true só se todo o checklist está coberto e não há mais nenhuma pergunta útil",
    )
