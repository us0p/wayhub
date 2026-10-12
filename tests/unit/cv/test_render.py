"""CV rendering helpers (D10, D81): headings and dates in the CV's language, file names."""

import uuid

import pytest

from mentor.cv import render
from mentor.cv.schemas import Contact, CVContent, ExperienceEntry, Line


def _entry(start: str | None, end: str | None = None, current: bool = False) -> ExperienceEntry:
    return ExperienceEntry(
        fact_id=uuid.uuid4(), title=Line(text="Dev"), start=start, end=end, current=current
    )


@pytest.mark.parametrize(
    ("language", "entry", "expected"),
    [
        ("pt-BR", _entry("2022-01", current=True), "jan 2022 – atual"),  # noqa: RUF001
        ("en", _entry("2019-03", "2021-12"), "Mar 2019 – Dec 2021"),  # noqa: RUF001
        ("es", _entry("2020-09", "2020-09"), "sept 2020 – sept 2020"),  # noqa: RUF001
        ("en", _entry(None, "2021-12"), "Dec 2021"),
        ("pt-BR", _entry(None), ""),
    ],
)
def test_periods_follow_the_cv_language(
    language: str, entry: ExperienceEntry, expected: str
) -> None:
    assert render.period(entry, render.labels(language)) == expected


def test_headings_fall_back_to_english() -> None:
    assert render.labels("pt-BR").experience == "Experiência"
    assert render.labels("es").skills == "Habilidades"
    assert render.labels("de").experience == "Experience"


def test_file_names_are_ascii_and_safe() -> None:
    content = CVContent(language="pt-BR", contact=Contact(name="Ana Conceição"))

    assert render.filename(content, 'Dev "Sênior"/Backend', "pdf") == (
        "CV - Ana Conceicao - Dev SeniorBackend.pdf"
    )
    assert render.filename(CVContent(language="en", contact=Contact()), None, "docx") == "CV.docx"
