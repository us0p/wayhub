"""Structured output of the job extraction call (M7). Flat and simple, so it maps cleanly to
the provider's JSON-schema support."""

from pydantic import BaseModel, Field

from mentor.jobs.models import RequirementImportance, RequirementKind
from mentor.profile.models import Seniority, WorkMode


class ParsedRequirement(BaseModel):
    importance: RequirementImportance = Field(
        description="must = obrigatório/exigido; nice = desejável/diferencial/plus"
    )
    kind: RequirementKind
    text: str = Field(description="O requisito como a vaga diz, curto, no idioma da vaga")
    skill: str | None = Field(
        None,
        description="Para kind=skill: o nome canônico e mais usado (JavaScript, não JS); "
        "senão null",
    )
    min_years: float | None = Field(
        None, description="Anos mínimos que a vaga pede para este requisito; null se não pede"
    )


class ParsedJob(BaseModel):
    is_job_posting: bool = Field(
        description="false se o conteúdo não é um anúncio de vaga de emprego"
    )
    title: str | None = Field(None, description="Cargo, como a vaga diz")
    company: str | None = None
    location: str | None = Field(None, description="Cidade/estado/país, se a vaga diz")
    work_mode: WorkMode | None = None
    seniority: Seniority | None = None
    language: str | None = Field(
        None, description="Idioma em que a vaga está escrita, código BCP 47: pt-BR, en, es..."
    )
    is_tech: bool | None = Field(
        None, description="true se é uma vaga de tecnologia (desenvolvimento, dados, infra...)"
    )
    summary: str | None = Field(
        None, description="Resumo da vaga em 1 ou 2 frases, em português do Brasil"
    )
    requirements: list[ParsedRequirement] = Field(
        default_factory=list,
        description="Todos os requisitos da vaga, cada um separado, na ordem em que aparecem",
    )
