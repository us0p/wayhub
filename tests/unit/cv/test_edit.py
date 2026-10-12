"""Addressing and changing CV items in the editor (M9b), and what a CV shows once some are
hidden."""

import uuid

import pytest

from mentor.cv import edit
from mentor.cv.schemas import Contact, CVContent, Entry, ExperienceEntry, Line


def _content() -> CVContent:
    def line(text: str) -> Line:
        return Line(text=text, source_ids=[uuid.uuid4()])

    return CVContent(
        language="en",
        contact=Contact(name="Ana"),
        headline=line("Python Developer"),
        experiences=[
            ExperienceEntry(
                fact_id=uuid.uuid4(), title=line("Developer"), bullets=[line("A"), line("B")]
            )
        ],
        skills=[Entry(fact_id=uuid.uuid4(), text="Python")],
    )


@pytest.mark.parametrize(
    ("path", "block", "editable", "hideable"),
    [
        ("headline", "headline", True, True),
        ("experiences.0", "experiences.0", False, True),
        ("experiences.0.title", "experiences.0", True, False),
        ("experiences.0.bullets.1", "experiences.0", True, True),
        ("skills.0", "skills", False, True),
    ],
)
def test_items_are_found_by_path(path: str, block: str, editable: bool, hideable: bool) -> None:
    item = edit.find(_content(), path)

    assert item is not None
    assert (item.block, item.editable, item.hideable) == (block, editable, hideable)


@pytest.mark.parametrize(
    "path",
    [
        "summary",
        "experiences.1",
        "experiences.0.bullets.2",
        "experiences.0.company",
        "skills.x",
        "skills.-1",
        "education.0",
        "contact",
        "",
        "headline.0",
    ],
)
def test_missing_items_are_none(path: str) -> None:
    assert edit.find(_content(), path) is None


def test_editing_marks_the_text_as_the_users_and_hiding_leaves_it_out() -> None:
    content = _content()
    assert not edit.edited(content)

    edit.set_text(edit.find(content, "experiences.0.bullets.0"), "A, better")  # type: ignore[arg-type]
    edit.set_hidden(edit.find(content, "experiences.0.bullets.1"), True)  # type: ignore[arg-type]
    edit.set_hidden(edit.find(content, "headline"), True)  # type: ignore[arg-type]

    assert edit.edited(content)
    assert content.experiences[0].bullets[0].user_edited
    visible = content.visible()
    assert visible.headline is None
    assert [b.text for b in visible.experiences[0].bullets] == ["A, better"]
    assert len(content.experiences[0].bullets) == 2  # the stored version keeps everything
    assert edit.blocks(content) == ["headline", "experiences.0", "skills"]
