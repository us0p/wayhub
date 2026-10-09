"""The profile review/edit screen (D15, D60, D61): every kind of fact can be edited and removed
by hand, only on the user's own data. Nothing can be added by hand: that goes through the
interview."""

import uuid
from datetime import date

from httpx import AsyncClient, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from mentor.ai.adapters.fake import FakeAI
from mentor.auth.models import User
from mentor.interview.models import Interview, InterviewStatus
from mentor.profile import skills
from mentor.profile.models import (
    Education,
    Experience,
    ExperienceBullet,
    Language,
    Link,
    Profile,
    Seniority,
    UserSkill,
    WorkMode,
)
from mentor.profile.service import stamps

from .helpers import login_and_consent


async def _user(db: AsyncSession, email: str) -> User:
    user = await db.scalar(select(User).where(User.email == email))
    assert user is not None
    return user


async def _post(client: AsyncClient, token: str, path: str, **data: str | list[str]) -> Response:
    return await client.post(path, data=data, headers={"X-CSRF-Token": token})


async def _experience(db: AsyncSession, user: User, *bullets: str) -> Experience:
    experience = Experience(
        user_id=user.id, company="Acme", title="Dev", start_date=date(2020, 1, 1)
    )
    db.add(experience)
    await db.flush()
    for text, created in zip(bullets, stamps(len(bullets)), strict=True):
        db.add(
            ExperienceBullet(
                user_id=user.id,
                experience_id=experience.id,
                text=text,
                embedding=[0.1] * 768,
                created_at=created,
            )
        )
    await db.flush()
    await db.refresh(experience, ["bullets"])
    return experience


async def _skill(db: AsyncSession, fake_ai: FakeAI, user: User, name: str) -> UserSkill:
    skill = await skills.normalize(db, fake_ai.embeddings, name)
    user_skill = UserSkill(user_id=user.id, skill_id=skill.id, raw_name=name, experience_ids=[])
    db.add(user_skill)
    await db.flush()
    return user_skill


# --- page ----------------------------------------------------------------------------------


async def test_profile_page_lists_the_facts_and_links_to_the_account(
    client: AsyncClient, db: AsyncSession
) -> None:
    await login_and_consent(client, "page@example.com")
    user = await _user(db, "page@example.com")
    db.add(Profile(user_id=user.id, full_name="Ana Souza", seniority=Seniority.MID))
    db.add(Language(user_id=user.id, language="Inglês", level="B2"))
    db.add(Link(user_id=user.id, kind="github", url="https://github.com/ana"))
    await _experience(db, user, "Criou APIs")

    page = await client.get("/perfil")

    assert page.status_code == 200
    for expected in (
        "Ana Souza",
        "Pleno",
        "Inglês",
        "https://github.com/ana",
        "Criou APIs",
        "Acme",
    ):
        assert expected in page.text
    assert 'href="/conta"' in page.text
    assert 'aria-current="page"' in page.text


async def test_empty_profile_page_invites_to_start_the_interview(client: AsyncClient) -> None:
    await login_and_consent(client, "empty@example.com")

    page = await client.get("/perfil")

    assert "Nenhuma experiência ainda." in page.text
    assert "Sua entrevista ainda não terminou" in page.text
    assert 'href="/entrevista"' in page.text and "Começar entrevista" in page.text
    assert "/perfil/experiencias/nova" not in page.text and "Adicionar experiência" not in page.text


async def test_finished_interview_offers_one_button_back_to_the_chat(
    client: AsyncClient, db: AsyncSession
) -> None:
    await login_and_consent(client, "done@example.com")
    user = await _user(db, "done@example.com")
    db.add(Interview(user_id=user.id, status=InterviewStatus.COMPLETE))
    await db.flush()

    page = await client.get("/perfil")

    assert page.text.count("Adicionar ou alterar detalhes") == 1
    assert 'hx-post="/entrevista/nova"' in page.text
    assert "Adicionar experiência" not in page.text


# --- contact and preferences ---------------------------------------------------------------


async def test_contact_is_edited_in_place(client: AsyncClient, db: AsyncSession) -> None:
    token = await login_and_consent(client, "contact@example.com")
    user = await _user(db, "contact@example.com")

    form = await client.get("/perfil/contato/editar")
    assert 'hx-post="/perfil/contato"' in form.text

    saved = await _post(
        client,
        token,
        "/perfil/contato",
        full_name="Ana  Souza",
        email="ana@exemplo.com",
        city="São Paulo",
        seniority="senior",
        work_modes=["remote", "hybrid"],
        target_roles="Backend, Dados, Backend",
        open_to_relocation="nao",
    )

    assert saved.status_code == 200
    assert "Ana Souza" in saved.text and "Sênior" in saved.text and "Remoto, Híbrido" in saved.text
    profile = await db.get(Profile, user.id)
    assert profile is not None
    await db.refresh(profile)
    assert profile.full_name == "Ana Souza"
    assert profile.seniority is Seniority.SENIOR
    assert profile.work_modes == [WorkMode.REMOTE, WorkMode.HYBRID]
    assert profile.target_roles == ["Backend", "Dados"]
    assert profile.open_to_relocation is False
    assert profile.source_turn_id is None


async def test_contact_can_clear_a_field(client: AsyncClient, db: AsyncSession) -> None:
    token = await login_and_consent(client, "clear@example.com")
    user = await _user(db, "clear@example.com")
    db.add(Profile(user_id=user.id, phone="11 99999-0000", seniority=Seniority.JUNIOR))
    await db.flush()

    await _post(client, token, "/perfil/contato", full_name="Ana", phone="", seniority="")

    profile = await db.get(Profile, user.id)
    assert profile is not None
    await db.refresh(profile)
    assert profile.phone is None and profile.seniority is None


async def test_contact_errors_show_the_form_again_as_typed(
    client: AsyncClient, db: AsyncSession
) -> None:
    token = await login_and_consent(client, "bad@example.com")
    user = await _user(db, "bad@example.com")

    response = await _post(
        client, token, "/perfil/contato", full_name="Ana", email="sem-arroba", seniority="deus"
    )

    assert response.status_code == 200
    assert "Informe um e-mail válido." in response.text
    assert "Escolha uma das opções." in response.text
    assert 'value="sem-arroba"' in response.text and 'value="Ana"' in response.text
    assert await db.get(Profile, user.id) is None


# --- experiences ---------------------------------------------------------------------------


async def test_editing_an_experience_saves_and_embeds_the_bullets(
    client: AsyncClient, db: AsyncSession
) -> None:
    token = await login_and_consent(client, "exp@example.com")
    user = await _user(db, "exp@example.com")
    experience = await _experience(db, user)

    form = await client.get(f"/perfil/experiencias/{experience.id}/editar")
    assert f'hx-post="/perfil/experiencias/{experience.id}"' in form.text
    response = await _post(
        client,
        token,
        f"/perfil/experiencias/{experience.id}",
        company="Acme",
        title="Dev backend",
        start_date="2021-03",
        end_date="2023-07",
        is_technical="sim",
        bullets="Criou APIs\n\n  Reduziu custos em 20%  \n",
    )

    assert response.status_code == 200
    assert "Dev backend" in response.text and "03/2021 \u2013 07/2023" in response.text
    await db.refresh(experience, ["bullets"])
    assert experience.start_date == date(2021, 3, 1) and experience.is_technical is True
    assert [b.text for b in experience.bullets] == ["Criou APIs", "Reduziu custos em 20%"]
    assert all(b.embedding is not None and b.source_turn_id is None for b in experience.bullets)


async def test_editing_bullets_keeps_ids_and_reembeds_only_changes(
    client: AsyncClient, db: AsyncSession, fake_ai: FakeAI
) -> None:
    token = await login_and_consent(client, "bul@example.com")
    user = await _user(db, "bul@example.com")
    experience = await _experience(db, user, "um", "dois", "três")
    first, second = experience.bullets[0], experience.bullets[1]
    fake_ai.embeddings.calls.clear()

    await _post(
        client,
        token,
        f"/perfil/experiencias/{experience.id}",
        company="Acme",
        title="Dev",
        bullets="um\nDOIS corrigido\nquatro\ncinco",
    )

    await db.refresh(experience, ["bullets"])
    assert [b.text for b in experience.bullets] == ["um", "DOIS corrigido", "quatro", "cinco"]
    assert experience.bullets[0].id == first.id and experience.bullets[1].id == second.id
    embedded = [text for call in fake_ai.embeddings.calls for text in call]
    assert sorted(embedded) == ["DOIS corrigido", "cinco", "quatro"]


async def test_fewer_lines_remove_the_last_bullets(client: AsyncClient, db: AsyncSession) -> None:
    token = await login_and_consent(client, "fewer@example.com")
    user = await _user(db, "fewer@example.com")
    experience = await _experience(db, user, "um", "dois")

    await _post(
        client, token, f"/perfil/experiencias/{experience.id}", company="Acme", bullets="um"
    )

    await db.refresh(experience, ["bullets"])
    assert [b.text for b in experience.bullets] == ["um"]


async def test_experience_errors(client: AsyncClient, db: AsyncSession) -> None:
    token = await login_and_consent(client, "experr@example.com")
    user = await _user(db, "experr@example.com")
    experience = await _experience(db, user, "um")
    path = f"/perfil/experiencias/{experience.id}"

    nothing = await _post(client, token, path, bullets="x")
    backwards = await _post(
        client, token, path, company="A", start_date="2022-05", end_date="2021-01"
    )
    garbage = await _post(client, token, path, company="A", start_date="ontem")

    assert "Informe a empresa ou o cargo." in nothing.text
    assert "O fim não pode ser antes do início." in backwards.text
    assert "Informe mês e ano válidos." in garbage.text
    await db.refresh(experience)
    assert experience.company == "Acme"


async def test_current_job_has_no_end_date(client: AsyncClient, db: AsyncSession) -> None:
    token = await login_and_consent(client, "cur@example.com")
    user = await _user(db, "cur@example.com")
    experience = await _experience(db, user)

    await _post(
        client,
        token,
        f"/perfil/experiencias/{experience.id}",
        company="A",
        start_date="2022-01",
        end_date="2023-01",
        is_current="sim",
    )

    await db.refresh(experience)
    assert experience.is_current and experience.end_date is None


async def test_deleting_an_experience_unlinks_it_from_skills(
    client: AsyncClient, db: AsyncSession, fake_ai: FakeAI
) -> None:
    token = await login_and_consent(client, "del@example.com")
    user = await _user(db, "del@example.com")
    experience = await _experience(db, user, "um")
    skill = await _skill(db, fake_ai, user, f"Go {uuid.uuid4().hex[:6]}")
    skill.experience_ids = [experience.id]
    await db.flush()
    experience_id = experience.id

    response = await client.delete(
        f"/perfil/experiencias/{experience_id}", headers={"X-CSRF-Token": token}
    )

    assert response.status_code == 200 and response.text == ""
    assert await db.scalar(select(Experience).where(Experience.id == experience_id)) is None
    assert (
        await db.scalar(select(ExperienceBullet).where(ExperienceBullet.user_id == user.id)) is None
    )
    await db.refresh(skill)
    assert skill.experience_ids == []


# --- education, languages, links, skills ---------------------------------------------------


async def test_education_edit_and_delete(client: AsyncClient, db: AsyncSession) -> None:
    token = await login_and_consent(client, "edu@example.com")
    user = await _user(db, "edu@example.com")
    education = Education(user_id=user.id, institution="USP", degree="Bacharelado")
    db.add(education)
    await db.flush()
    path = f"/perfil/formacoes/{education.id}"

    saved = await _post(
        client, token, path, institution="UNICAMP", end_year="2019", status="completed"
    )
    assert "UNICAMP" in saved.text and "Concluído" in saved.text
    await db.refresh(education)
    assert education.institution == "UNICAMP" and education.degree is None

    bad = await _post(client, token, path, institution="X", end_year="1800")
    assert "Informe um ano entre 1950 e 2100." in bad.text

    await client.delete(path, headers={"X-CSRF-Token": token})
    assert await db.scalar(select(Education).where(Education.user_id == user.id)) is None


async def test_language_rejects_duplicates_ignoring_case(
    client: AsyncClient, db: AsyncSession
) -> None:
    token = await login_and_consent(client, "lang@example.com")
    user = await _user(db, "lang@example.com")
    english = Language(user_id=user.id, language="Inglês", level="B2")
    spanish = Language(user_id=user.id, language="Espanhol")
    db.add_all([english, spanish])
    await db.flush()

    duplicate = await _post(client, token, f"/perfil/idiomas/{spanish.id}", language="inglês")
    same = await _post(
        client, token, f"/perfil/idiomas/{english.id}", language="Inglês", level="C1"
    )

    assert "Esse idioma já está na lista." in duplicate.text
    await db.refresh(english)
    await db.refresh(spanish)
    assert english.level == "C1" and spanish.language == "Espanhol"
    assert same.status_code == 200


async def test_links_accept_only_http_urls(client: AsyncClient, db: AsyncSession) -> None:
    token = await login_and_consent(client, "link@example.com")
    user = await _user(db, "link@example.com")
    github = Link(user_id=user.id, kind="github", url="https://github.com/ana")
    other = Link(user_id=user.id, kind="other", url="https://ana.dev")
    db.add_all([github, other])
    await db.flush()

    ok = await _post(client, token, f"/perfil/links/{github.id}", kind="github", url="github.com/a")
    bad = await _post(
        client, token, f"/perfil/links/{other.id}", kind="other", url="javascript:alert(1)"
    )
    dup = await _post(
        client, token, f"/perfil/links/{other.id}", kind="other", url="https://github.com/a"
    )

    assert 'href="https://github.com/a"' in ok.text
    assert "Informe um endereço http ou https válido." in bad.text
    assert "Esse link já está na lista." in dup.text
    await db.refresh(other)
    assert other.url == "https://ana.dev"


async def test_skills_are_normalized_and_not_duplicated(
    client: AsyncClient, db: AsyncSession, fake_ai: FakeAI
) -> None:
    token = await login_and_consent(client, "skill@example.com")
    user = await _user(db, "skill@example.com")
    name = f"Rust {uuid.uuid4().hex[:6]}"
    mine = await _skill(db, fake_ai, user, f"Elm {uuid.uuid4().hex[:6]}")
    await _skill(db, fake_ai, user, name)
    path = f"/perfil/habilidades/{mine.id}"

    dup = await _post(client, token, path, name=name.upper())
    bad = await _post(client, token, path, name="X", years="muito")
    ok = await _post(client, token, path, name=name + "x", years="2,5", level="avançado")

    assert "Essa habilidade já está na lista." in dup.text
    assert "Informe um número." in bad.text
    assert "2.5 ano(s)" in ok.text
    await db.refresh(mine)
    assert mine.years == 2.5 and mine.source_turn_id is None


# --- access --------------------------------------------------------------------------------


async def test_cancelling_shows_the_card_again(client: AsyncClient, db: AsyncSession) -> None:
    await login_and_consent(client, "cancel@example.com")
    user = await _user(db, "cancel@example.com")
    experience = await _experience(db, user)

    card = await client.get(f"/perfil/experiencias/{experience.id}")

    assert "Dev" in card.text and f'id="item-{experience.id}"' in card.text
    assert "data-confirm=" in card.text and "hx-confirm" not in card.text  # a modal, not alert()


async def test_nobody_can_touch_another_users_facts(client: AsyncClient, db: AsyncSession) -> None:
    token = await login_and_consent(client, "mine@example.com")
    other = User(email="theirs@example.com", name="Outra", google_sub="sub-theirs")
    db.add(other)
    await db.flush()
    theirs = await _experience(db, other, "segredo")

    for method, path in (
        ("GET", f"/perfil/experiencias/{theirs.id}"),
        ("GET", f"/perfil/experiencias/{theirs.id}/editar"),
        ("POST", f"/perfil/experiencias/{theirs.id}"),
        ("DELETE", f"/perfil/experiencias/{theirs.id}"),
    ):
        response = await client.request(
            method, path, data={"company": "Hack"}, headers={"X-CSRF-Token": token}
        )
        assert response.status_code == 404, (method, path)

    await db.refresh(theirs)
    assert theirs.company == "Acme"
    assert (await client.get("/perfil/desconhecido/" + str(theirs.id))).status_code == 404


async def test_nothing_can_be_added_by_hand(client: AsyncClient) -> None:
    token = await login_and_consent(client, "noadd@example.com")

    for slug in ("experiencias", "formacoes", "idiomas", "links", "habilidades"):
        assert (await client.get(f"/perfil/{slug}/nova")).status_code in (404, 422)
        created = await _post(client, token, f"/perfil/{slug}", language="Inglês")
        assert created.status_code in (404, 405)
        assert (await client.get(f"/perfil/{slug}")).status_code in (404, 405)


async def test_edits_require_login_and_csrf(client: AsyncClient, db: AsyncSession) -> None:
    anonymous = await client.post("/perfil/contato", data={"full_name": "Ana"})
    assert anonymous.status_code in (303, 403)

    await login_and_consent(client, "csrf@example.com")
    no_token = await client.post("/perfil/contato", data={"full_name": "Ana"})
    assert no_token.status_code == 403
