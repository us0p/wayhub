"""CV schemas (M9, D81): what the generator answers, what the verifier answers, and the CV as
stored and rendered.

Every sentence the model writes carries the refs of the profile facts it is based on (Model A,
D12). Names, companies, dates, contact details and skill names are never written by the model:
they are copied from the profile when the CV is assembled.
"""

import uuid
from enum import StrEnum

from pydantic import BaseModel, Field

# -- the generator ---------------------------------------------------------------------------


class Sourced(BaseModel):
    text: str
    refs: list[str] = Field(
        default_factory=list,
        description="Códigos dos itens do perfil em que a frase se baseia (E1, B3, S2, F1, P1...).",
    )


class GeneratedExperience(BaseModel):
    ref: str = Field(description="O código da experiência (E1, E2...).")
    title: Sourced = Field(description="O cargo, no idioma do CV, fiel ao original.")
    bullets: list[Sourced]


class GeneratedItem(BaseModel):
    ref: str = Field(description="O código do item do perfil (F1, L1...).")
    text: str = Field(description="O item, no idioma do CV, fiel ao original.")


class GeneratedCV(BaseModel):
    headline: Sourced = Field(description="Uma linha com o cargo ou a área da pessoa.")
    summary: Sourced = Field(description="Resumo profissional de 2 a 3 frases.")
    experiences: list[GeneratedExperience] = Field(
        description="Só as experiências relevantes para a vaga, na ordem em que vão aparecer."
    )
    skill_refs: list[str] = Field(
        description="Códigos das habilidades relevantes (S1, S2...), na ordem em que vão aparecer."
    )
    education: list[GeneratedItem] = Field(default_factory=list)
    languages: list[GeneratedItem] = Field(default_factory=list)


# -- the verifier ----------------------------------------------------------------------------


class ClaimVerdict(StrEnum):
    SUPPORTED = "supported"
    REWRITE = "rewrite"  # partly supported: a corrected text replaces it
    REMOVE = "remove"


class CheckedClaim(BaseModel):
    ref: str = Field(description="O código da frase (C1, C2...).")
    verdict: ClaimVerdict
    revised: str | None = Field(
        default=None,
        description="Só para rewrite: a frase corrigida, só com o que as fontes dizem.",
    )
    reason: str | None = Field(
        default=None,
        description="Para rewrite e remove: o motivo, curto, em português do Brasil, falando "
        "com a pessoa (você).",
    )


class Verification(BaseModel):
    claims: list[CheckedClaim]


# -- the CV as stored (`cvs.content`) and rendered -------------------------------------------


class Line(BaseModel):
    text: str
    source_ids: list[uuid.UUID] = Field(default_factory=list)
    hidden: bool = False  # left out of the CV by the user (M9b)
    user_edited: bool = False  # the text is the user's own statement (D81), not verified


class Link(BaseModel):
    kind: str
    url: str


class Contact(BaseModel):
    name: str | None = None
    email: str | None = None
    phone: str | None = None
    location: str | None = None
    links: list[Link] = Field(default_factory=list)


class ExperienceEntry(BaseModel):
    fact_id: uuid.UUID
    title: Line
    company: str | None = None
    start: str | None = None  # YYYY-MM
    end: str | None = None
    current: bool = False
    bullets: list[Line] = Field(default_factory=list)
    hidden: bool = False


class Entry(BaseModel):
    """A skill, an education or a language line."""

    fact_id: uuid.UUID
    text: str
    detail: str | None = None  # the institution and years, for education
    hidden: bool = False
    user_edited: bool = False


class CVContent(BaseModel):
    language: str
    contact: Contact
    headline: Line | None = None
    summary: Line | None = None
    experiences: list[ExperienceEntry] = Field(default_factory=list)
    skills: list[Entry] = Field(default_factory=list)
    education: list[Entry] = Field(default_factory=list)
    languages: list[Entry] = Field(default_factory=list)

    def visible(self) -> "CVContent":
        """The CV as it's shown and downloaded: without what the user hid."""

        def shown[T: Line | Entry](items: list[T]) -> list[T]:
            return [item for item in items if not item.hidden]

        return self.model_copy(
            update={
                "headline": self.headline if self.headline and not self.headline.hidden else None,
                "summary": self.summary if self.summary and not self.summary.hidden else None,
                "experiences": [
                    e.model_copy(update={"bullets": shown(e.bullets)})
                    for e in self.experiences
                    if not e.hidden
                ],
                "skills": shown(self.skills),
                "education": shown(self.education),
                "languages": shown(self.languages),
            }
        )


class Change(BaseModel):
    """What the verification did to one sentence (shown to the user, D81)."""

    section: str  # headline, summary, experience, education, language
    original: str
    revised: str | None  # None: removed
    reason: str
