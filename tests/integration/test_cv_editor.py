"""The CV editor (M9b, D81): editing the text of a sentence and hiding items change the open
version in place; edited text is the user's own (not verified); hidden items are left out of
the page, the PDF and the DOCX; only the user's own, written CVs can be edited."""

import io
import uuid

import docx
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from pypdf import PdfReader
from sqlalchemy.ext.asyncio import AsyncSession

from mentor.ai.adapters.fake import FakeAI
from mentor.cv.models import CV
from mentor.cv.schemas import CVContent

from .helpers import csrf_from, login_and_consent
from .test_cv import RecordingQueue, _generate, _ready_cv, queue

__all__ = ["queue"]  # the fixture, used by name


async def _headers(client: AsyncClient, cv: CV) -> dict[str, str]:
    token = csrf_from((await client.get(f"/curriculos/{cv.id}/editar")).text)
    return {"X-CSRF-Token": token, "HX-Request": "true"}


async def _content(db: AsyncSession, cv: CV) -> CVContent:
    await db.refresh(cv)
    return CVContent.model_validate(cv.content)


async def test_the_editor_lists_every_block_of_the_cv(
    client: AsyncClient, db: AsyncSession, fake_ai: FakeAI, queue: RecordingQueue
) -> None:
    _, _, cv = await _ready_cv(client, db, fake_ai, queue)

    cv_page = (await client.get(f"/curriculos/{cv.id}")).text
    page = (await client.get(f"/curriculos/{cv.id}/editar")).text

    assert f'href="/curriculos/{cv.id}/editar"' in cv_page
    assert "Você editou esta versão" not in cv_page
    for block in ("headline", "summary", "experiences-0", "skills", "education"):
        assert f'id="cv-block-{block}"' in page
    assert 'id="cv-block-languages"' not in page  # the verifier removed the only one
    assert "Built REST APIs with Django" in page and "eu não confiro as suas edições" in page
    assert f'hx-get="/curriculos/{cv.id}/itens/experiences.0.bullets.0/editar"' in page


async def test_editing_a_sentence_changes_this_version_and_isnt_verified(
    client: AsyncClient, db: AsyncSession, fake_ai: FakeAI, queue: RecordingQueue
) -> None:
    _, _, cv = await _ready_cv(client, db, fake_ai, queue)
    headers = await _headers(client, cv)
    calls = len(fake_ai.chat.calls)
    path = f"/curriculos/{cv.id}/itens/experiences.0.bullets.0"

    form = await client.get(f"{path}/editar")
    saved = await client.post(
        path, data={"texto": "  Built  REST APIs with Django and FastAPI "}, headers=headers
    )

    assert 'name="texto"' in form.text and "Built REST APIs with Django</textarea>" in form.text
    assert saved.status_code == 200 and 'id="cv-block-experiences-0"' in saved.text
    assert "Built REST APIs with Django and FastAPI" in saved.text
    assert "Editado por você" in saved.text and 'name="texto"' not in saved.text
    content = await _content(db, cv)
    bullet = content.experiences[0].bullets[0]
    assert bullet.text == "Built REST APIs with Django and FastAPI" and bullet.user_edited
    assert bullet.source_ids  # kept for reference
    assert len(fake_ai.chat.calls) == calls  # the user's own statement: not verified
    assert "Você editou esta versão" in (await client.get(f"/curriculos/{cv.id}")).text
    pdf = await client.get(f"/curriculos/{cv.id}/pdf")
    text = "\n".join(p.extract_text() for p in PdfReader(io.BytesIO(pdf.content)).pages)
    assert "Built REST APIs with Django and FastAPI" in text


async def test_saving_the_same_text_is_not_an_edit(
    client: AsyncClient, db: AsyncSession, fake_ai: FakeAI, queue: RecordingQueue
) -> None:
    _, _, cv = await _ready_cv(client, db, fake_ai, queue)
    headers = await _headers(client, cv)

    await client.post(
        f"/curriculos/{cv.id}/itens/headline", data={"texto": "Python Developer"}, headers=headers
    )

    content = await _content(db, cv)
    assert content.headline is not None and not content.headline.user_edited


async def test_an_empty_or_too_long_text_is_refused_and_the_form_comes_back(
    client: AsyncClient, db: AsyncSession, fake_ai: FakeAI, queue: RecordingQueue
) -> None:
    _, _, cv = await _ready_cv(client, db, fake_ai, queue)
    headers = await _headers(client, cv)
    path = f"/curriculos/{cv.id}/itens/headline"

    empty = await client.post(path, data={"texto": "   "}, headers=headers)
    long = await client.post(path, data={"texto": "x" * 201}, headers=headers)

    assert empty.status_code == 200 and "Escreva o texto, ou oculte este item." in empty.text
    assert 'name="texto"' in empty.text and 'aria-invalid="true"' in empty.text
    assert "Use no máximo 200 caracteres." in long.text and "x" * 201 in long.text
    content = await _content(db, cv)
    assert content.headline is not None and content.headline.text == "Python Developer"


async def test_hidden_items_leave_the_page_the_pdf_and_the_docx(
    client: AsyncClient, db: AsyncSession, fake_ai: FakeAI, queue: RecordingQueue
) -> None:
    _, _, cv = await _ready_cv(client, db, fake_ai, queue)
    headers = await _headers(client, cv)
    base = f"/curriculos/{cv.id}/itens"

    bullet = await client.post(f"{base}/experiences.0.bullets.0/ocultar", headers=headers)
    skill = await client.post(f"{base}/skills.0/ocultar", headers=headers)
    await client.post(f"{base}/summary/ocultar", headers=headers)

    assert "Oculto" in bullet.text and f"{base}/experiences.0.bullets.0/mostrar" in bullet.text
    assert 'aria-pressed="false"' in skill.text
    page = (await client.get(f"/curriculos/{cv.id}")).text
    document = page.split("<article", 1)[1].split("</article>", 1)[0]
    assert "Built REST APIs" not in document and "Developer with 4 years" not in document
    assert "Python Developer" in document  # the rest stays
    pdf = await client.get(f"/curriculos/{cv.id}/pdf")
    text = "\n".join(p.extract_text() for p in PdfReader(io.BytesIO(pdf.content)).pages)
    assert "Built REST APIs" not in text and "SKILLS" not in text and "SUMMARY" not in text
    word = await client.get(f"/curriculos/{cv.id}/docx")
    paragraphs = [p.text for p in docx.Document(io.BytesIO(word.content)).paragraphs]
    assert "Built REST APIs with Django" not in paragraphs and "SKILLS" not in paragraphs

    shown = await client.post(f"{base}/experiences.0.bullets.0/mostrar", headers=headers)

    assert "Oculto" not in shown.text
    content = await _content(db, cv)
    assert not content.experiences[0].bullets[0].hidden and content.skills[0].hidden


async def test_hiding_an_experience_hides_it_with_its_bullets(
    client: AsyncClient, db: AsyncSession, fake_ai: FakeAI, queue: RecordingQueue
) -> None:
    _, _, cv = await _ready_cv(client, db, fake_ai, queue)
    headers = await _headers(client, cv)

    block = await client.post(f"/curriculos/{cv.id}/itens/experiences.0/ocultar", headers=headers)

    assert "Esta experiência não aparece no currículo." in block.text
    assert "Built REST APIs" not in block.text
    pdf = await client.get(f"/curriculos/{cv.id}/pdf")
    text = "\n".join(p.extract_text() for p in PdfReader(io.BytesIO(pdf.content)).pages)
    assert "EXPERIENCE" not in text and "Acme" not in text


async def test_unknown_or_unsupported_items_are_not_found(
    client: AsyncClient, db: AsyncSession, fake_ai: FakeAI, queue: RecordingQueue
) -> None:
    _, _, cv = await _ready_cv(client, db, fake_ai, queue)
    headers = await _headers(client, cv)
    base = f"/curriculos/{cv.id}/itens"

    for path in ("experiences.5", "experiences.0.bullets.9", "skills.-1", "contact", "languages.0"):
        assert (await client.get(f"{base}/{path}")).status_code == 404
    assert (await client.get(f"{base}/skills.0/editar")).status_code == 404  # names: profile
    assert (await client.get(f"{base}/experiences.0/editar")).status_code == 404
    assert (await client.post(f"{base}/skills.0", headers=headers)).status_code == 404
    title = await client.post(f"{base}/experiences.0.title/ocultar", headers=headers)
    assert title.status_code == 404
    assert (await client.post(f"{base}/headline/apagar", headers=headers)).status_code == 404


async def test_only_the_users_own_written_cvs_can_be_edited(
    app: FastAPI, client: AsyncClient, db: AsyncSession, fake_ai: FakeAI, queue: RecordingQueue
) -> None:
    _, job, cv = await _ready_cv(client, db, fake_ai, queue)

    # A version still being written: the editor sends back to its page.
    await _generate(client, job)
    pending = queue.cvs[-1]
    editor = await client.get(f"/curriculos/{pending}/editar", follow_redirects=False)
    assert editor.status_code in (302, 303) and editor.headers["location"] == (
        f"/curriculos/{pending}"
    )
    assert (await client.get(f"/curriculos/{pending}/itens/headline")).status_code == 404

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as other:
        await login_and_consent(other, f"outro-{uuid.uuid4().hex[:8]}@example.com", "tester")
        token = csrf_from((await other.get("/vagas")).text)
        page = await other.get(f"/curriculos/{cv.id}/editar")
        save = await other.post(
            f"/curriculos/{cv.id}/itens/headline",
            data={"texto": "Hacked"},
            headers={"X-CSRF-Token": token, "HX-Request": "true"},
        )

    assert (page.status_code, save.status_code) == (404, 404)
    content = await _content(db, cv)
    assert content.headline is not None and content.headline.text == "Python Developer"
