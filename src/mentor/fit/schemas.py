"""What the model answers when it compares a posting with the profile (M8)."""

from pydantic import BaseModel, Field

from mentor.fit.models import Verdict


class JudgedRequirement(BaseModel):
    ref: str = Field(description="O código do requisito, por exemplo R1.")
    verdict: Verdict = Field(
        description="met: o perfil mostra que atende. partial: atende em parte. gap: o perfil "
        "não mostra. unknown: o perfil não fala desse assunto."
    )
    message: str = Field(
        description="Uma frase curta e factual, em português do Brasil, falando com a pessoa "
        "(você). Cite os números quando houver anos."
    )
    evidence_refs: list[str] = Field(
        default_factory=list,
        description="Códigos dos itens do perfil que sustentam o veredito (E1, S2, F1, L1...).",
    )


class FitJudgement(BaseModel):
    requirements: list[JudgedRequirement]
