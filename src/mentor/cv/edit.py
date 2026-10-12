"""Editing a CV version (M9b, D81): the user edits the text of a sentence or hides an item
(the headline, the summary, an experience, a bullet, a skill, an education or language line).
Nothing is added, removed or reordered, so an item is addressed by its place in the content:
`headline`, `summary`, `experiences.0`, `experiences.0.title`, `experiences.0.bullets.2`,
`skills.3`, `education.1`, `languages.0`.

Edits change the open version in place. Edited text is the user's own statement (`user_edited`):
it isn't verified, and its source facts are kept for reference. Names, companies, dates, contact
details and skill names come from the profile and are edited there, not here.
"""

from dataclasses import dataclass

from mentor.cv.schemas import CVContent, Entry, ExperienceEntry, Line

# Longest text accepted per kind of sentence.
LIMITS = {
    "headline": 200,
    "summary": 1500,
    "title": 150,
    "bullet": 1000,
    "education": 300,
    "language": 150,
}


@dataclass(frozen=True)
class Item:
    path: str
    block: str  # the part of the editor re-rendered after a change
    node: Line | Entry | ExperienceEntry
    limit: int | None  # None: can only be hidden
    hideable: bool

    @property
    def editable(self) -> bool:
        return self.limit is not None


def _index[T](items: list[T], value: str) -> T | None:
    if not value.isdigit() or int(value) >= len(items):
        return None
    return items[int(value)]


def find(content: CVContent, path: str) -> Item | None:
    """The item at `path`, or None when there is no such item."""
    match path.split("."):
        case ["headline" | "summary" as key]:
            line = content.headline if key == "headline" else content.summary
            return Item(path, key, line, LIMITS[key], True) if line else None
        case ["experiences", i, *rest]:
            if (entry := _index(content.experiences, i)) is None:
                return None
            block = f"experiences.{i}"
            match rest:
                case []:
                    return Item(path, block, entry, None, True)
                case ["title"]:
                    return Item(path, block, entry.title, LIMITS["title"], False)
                case ["bullets", j] if (bullet := _index(entry.bullets, j)) is not None:
                    return Item(path, block, bullet, LIMITS["bullet"], True)
            return None
        case ["skills", i] if (skill := _index(content.skills, i)) is not None:
            return Item(path, "skills", skill, None, True)
        case ["education", i] if (item := _index(content.education, i)) is not None:
            return Item(path, "education", item, LIMITS["education"], True)
        case ["languages", i] if (item := _index(content.languages, i)) is not None:
            return Item(path, "languages", item, LIMITS["language"], True)
    return None


def blocks(content: CVContent) -> list[str]:
    """The editor's blocks, in the CV's order."""
    result = [key for key in ("headline", "summary") if getattr(content, key) is not None]
    result += [f"experiences.{i}" for i in range(len(content.experiences))]
    return result + [key for key in ("skills", "education", "languages") if getattr(content, key)]


def set_text(item: Item, text: str) -> None:
    node = item.node
    assert item.editable and not isinstance(node, ExperienceEntry)
    if text != node.text:
        node.text = text
        node.user_edited = True


def set_hidden(item: Item, hidden: bool) -> None:
    assert item.hideable
    item.node.hidden = hidden


def edited(content: CVContent) -> bool:
    """Whether the user changed anything in this version."""
    lines: list[Line | Entry | ExperienceEntry] = [
        *(x for x in (content.headline, content.summary) if x),
        *content.experiences,
        *(e.title for e in content.experiences),
        *(b for e in content.experiences for b in e.bullets),
        *content.skills,
        *content.education,
        *content.languages,
    ]
    return any(x.hidden or getattr(x, "user_edited", False) for x in lines)
