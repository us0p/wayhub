"""The interview checklist (D14): what the agent must cover before the interview can end.

Labels are user-facing (pt-BR, through `_`); `goal` tells the models what "done" means.
"""

from dataclasses import dataclass
from enum import StrEnum

from mentor.i18n import gettext_ as _
from mentor.interview.models import ChecklistStatus


class ChecklistItem(StrEnum):
    IDENTITY = "identity"
    LOCATION = "location"
    OBJECTIVE = "objective"
    SENIORITY = "seniority"
    WORK_MODE = "work_mode"
    EXPERIENCES = "experiences"
    SKILLS = "skills"
    EDUCATION = "education"
    LANGUAGES = "languages"
    LINKS = "links"


@dataclass(frozen=True)
class ItemSpec:
    label: str
    goal: str  # for the prompts; not shown to the user


SPECS: dict[ChecklistItem, ItemSpec] = {
    ChecklistItem.IDENTITY: ItemSpec(
        _("Nome e contato"), "nome completo, e-mail e telefone de contato para o CV"
    ),
    ChecklistItem.LOCATION: ItemSpec(
        _("Localização"), "cidade e estado onde mora; se aceita mudar de cidade"
    ),
    ChecklistItem.OBJECTIVE: ItemSpec(
        _("Objetivo"), "cargos e área que busca (ex.: desenvolvedor backend, dados, design)"
    ),
    ChecklistItem.SENIORITY: ItemSpec(
        _("Senioridade"), "nível em que se vê hoje (estágio, júnior, pleno, sênior, ...)"
    ),
    ChecklistItem.WORK_MODE: ItemSpec(
        _("Modalidade"), "remoto, híbrido e/ou presencial, e em quais cidades"
    ),
    ChecklistItem.EXPERIENCES: ItemSpec(
        _("Experiências"),
        "todas as experiências relevantes (inclusive não técnicas): empresa, cargo, início e "
        "fim (mês/ano), o que fazia, tecnologias e resultados",
    ),
    ChecklistItem.SKILLS: ItemSpec(
        _("Habilidades"), "tecnologias e ferramentas, com tempo de uso ou nível"
    ),
    ChecklistItem.EDUCATION: ItemSpec(
        _("Formação"), "cursos superiores/técnicos e certificações, com status e datas"
    ),
    ChecklistItem.LANGUAGES: ItemSpec(_("Idiomas"), "idiomas e nível em cada um"),
    ChecklistItem.LINKS: ItemSpec(
        _("Links"), "LinkedIn, GitHub, portfólio (ou confirmação de que não tem)"
    ),
}

_WEIGHT = {ChecklistStatus.MISSING: 0.0, ChecklistStatus.PARTIAL: 0.5, ChecklistStatus.DONE: 1.0}


@dataclass(frozen=True)
class ItemState:
    item: ChecklistItem
    status: ChecklistStatus
    note: str | None = None

    @property
    def label(self) -> str:
        return SPECS[self.item].label


@dataclass(frozen=True)
class Progress:
    items: tuple[ItemState, ...]

    @property
    def percent(self) -> int:
        return round(100 * sum(_WEIGHT[i.status] for i in self.items) / len(self.items))

    @property
    def all_done(self) -> bool:
        return all(i.status is ChecklistStatus.DONE for i in self.items)

    @property
    def open_items(self) -> tuple[ItemState, ...]:
        return tuple(i for i in self.items if i.status is not ChecklistStatus.DONE)
