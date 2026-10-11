"""Years of experience from the profile's dates (M8, D80), computed in Python rather than by the
model: per skill (the experiences it was used in) and overall, with overlapping periods counted
once. Dates have month precision (D47); a current job runs until this month."""

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date

from mentor.profile.models import Experience
from mentor.profile.service import Snapshot

type Period = tuple[int, int]  # [first month, month after the last), as year * 12 + month


def _index(value: date) -> int:
    return value.year * 12 + value.month - 1


def period(exp: Experience, today: date) -> Period | None:
    """The months an experience covers, both ends included; None without usable dates."""
    if exp.start_date is None:
        return None
    end = today if exp.is_current else exp.end_date
    if end is None:
        return None
    first, last = _index(exp.start_date), _index(min(end, today))
    return (first, last + 1) if last >= first else None


def months(periods: Iterable[Period | None]) -> int:
    """Months covered by the periods together: overlaps count once."""
    total, reach = 0, None
    for first, end in sorted(p for p in periods if p is not None):
        if reach is not None and first < reach:
            first = reach
        if end > first:
            total += end - first
            reach = end if reach is None else max(reach, end)
    return total


def describe(count: int) -> str:
    """'3 anos e 2 meses', '1 ano', '8 meses' (pt-BR, for prompts)."""
    years, rest = divmod(count, 12)
    parts = []
    if years:
        parts.append(f"{years} ano" if years == 1 else f"{years} anos")
    if rest or not years:
        parts.append(f"{rest} mês" if rest == 1 else f"{rest} meses")
    return " e ".join(parts)


@dataclass(frozen=True)
class Totals:
    all_months: int  # every experience
    technical_months: int  # experiences marked technical
    # Per user skill (by ref): months of the experiences it was used in.
    skill_months: dict[str, int]


def totals(snap: Snapshot, today: date) -> Totals:
    by_id = {exp.id: exp for exp in snap.experiences}
    return Totals(
        all_months=months(period(exp, today) for exp in snap.experiences),
        technical_months=months(period(exp, today) for exp in snap.experiences if exp.is_technical),
        skill_months={
            snap.ref_of(us): months(
                period(by_id[i], today) for i in us.experience_ids if i in by_id
            )
            for us in snap.skills
            if any(i in by_id for i in us.experience_ids)
        },
    )


def render(snap: Snapshot, today: date) -> str:
    """The computed figures as prompt text, next to the profile."""
    figures = totals(snap, today)
    lines = [
        "Tempo de experiência calculado pelas datas (períodos sobrepostos contam uma vez):",
        f"- Total: {describe(figures.all_months)}",
        f"- Em experiências técnicas: {describe(figures.technical_months)}",
    ]
    for us in snap.skills:
        ref = snap.ref_of(us)
        declared = f"declarou {us.years:g} anos" if us.years is not None else "não declarou anos"
        if ref in figures.skill_months:
            used_in = ", ".join(
                snap.ref_of(exp) for exp in snap.experiences if exp.id in us.experience_ids
            )
            computed = f"; pelas datas das experiências em que usou ({used_in}): " + describe(
                figures.skill_months[ref]
            )
        else:
            computed = ""
        lines.append(f"- [{ref}] {us.skill.name}: {declared}{computed}")
    return "\n".join(lines)
