"""Years of experience from the profile's dates (D80): overlaps count once, a current job runs
until this month, and wording for the prompt."""

import uuid
from datetime import date

import pytest

from mentor.auth.models import User
from mentor.fit import years
from mentor.profile.models import Experience, Skill, UserSkill
from mentor.profile.service import Snapshot

TODAY = date(2026, 10, 11)


def _exp(
    start: date | None, end: date | None = None, *, current: bool = False, tech: bool = True
) -> Experience:
    return Experience(
        id=uuid.uuid4(), start_date=start, end_date=end, is_current=current, is_technical=tech
    )


def test_a_period_includes_both_months() -> None:
    assert years.months([years.period(_exp(date(2020, 1, 1), date(2020, 12, 1)), TODAY)]) == 12
    assert years.months([years.period(_exp(date(2026, 10, 1), current=True), TODAY)]) == 1


def test_overlapping_periods_count_once() -> None:
    periods = [
        years.period(_exp(date(2020, 1, 1), date(2021, 12, 1)), TODAY),  # 24 months
        years.period(_exp(date(2021, 1, 1), date(2022, 6, 1)), TODAY),  # overlaps 12, adds 6
        years.period(_exp(date(2020, 3, 1), date(2020, 5, 1)), TODAY),  # inside the first
        years.period(_exp(date(2024, 1, 1), date(2024, 1, 1)), TODAY),  # a gap, then 1
    ]
    assert years.months(periods) == 31


def test_experiences_without_usable_dates_are_left_out() -> None:
    assert years.period(_exp(None, date(2020, 1, 1)), TODAY) is None
    assert years.period(_exp(date(2020, 1, 1)), TODAY) is None  # no end and not current
    assert years.period(_exp(date(2022, 1, 1), date(2021, 1, 1)), TODAY) is None
    # A future end date counts only until this month.
    assert years.months([years.period(_exp(date(2026, 1, 1), date(2027, 6, 1)), TODAY)]) == 10


@pytest.mark.parametrize(
    ("count", "text"),
    [
        (0, "0 meses"),
        (1, "1 mês"),
        (8, "8 meses"),
        (12, "1 ano"),
        (14, "1 ano e 2 meses"),
        (37, "3 anos e 1 mês"),
    ],
)
def test_describe(count: int, text: str) -> None:
    assert years.describe(count) == text


def test_totals_per_skill_and_technical_time() -> None:
    dev = _exp(date(2022, 1, 1), current=True)  # 58 months until 2026-10
    shop = _exp(date(2018, 1, 1), date(2019, 12, 1), tech=False)  # 24 months
    python = UserSkill(
        id=uuid.uuid4(), years=3, experience_ids=[dev.id], skill=Skill(name="Python")
    )
    excel = UserSkill(id=uuid.uuid4(), years=None, experience_ids=[], skill=Skill(name="Excel"))
    snap = Snapshot(
        user=User(),
        profile=None,
        experiences=[dev, shop],
        education=[],
        languages=[],
        links=[],
        skills=[python, excel],
    )

    figures = years.totals(snap, TODAY)

    assert (figures.all_months, figures.technical_months) == (82, 58)
    assert figures.skill_months == {"S1": 58}
    text = years.render(snap, TODAY)
    assert "- Total: 6 anos e 10 meses" in text
    assert (
        "- [S1] Python: declarou 3 anos; pelas datas das experiências em que usou (E1): "
        "4 anos e 10 meses" in text
    )
    assert "- [S2] Excel: não declarou anos" in text
