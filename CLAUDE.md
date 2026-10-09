# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

Mentor: a candidate-focused career platform. The product scope (in Portuguese) is in `SCOPE.md`. The current work is the Phase 1 MVP: Google login, a conversational onboarding interview (real-time voice or text), and per-job CV generation with a fit warning. The implementation plan (10 milestones, with a status table) is in `docs/PLAN.md`; keep its status table current as milestones land.

## Decisions log (mandatory)

`docs/DECISIONS.md` records every product, design and technical decision (D1, D2, …). **Whenever a decision is made or changed with the user, append a new dated row in the same turn.** Never rewrite past rows; mark them `Superseded by Dxx` instead. Read it before making architectural choices: most "why is it like this?" answers are there.

## Commands

The Python toolchain is **uv** with Python 3.12 (`.python-version`). Dependencies are locked in `uv.lock`; use `uv sync --frozen`.

```bash
cp .env.example .env              # first time: fill in generated POSTGRES_PASSWORD / SECRET_KEY (compose refuses to start without them)
docker compose up                 # db (pgvector pg17) + app (runs migrations, autoreloads on 127.0.0.1:8000) + tailwind --watch
uv run pytest                     # unit + integration (integration needs a *migrated* Postgres at DATABASE_URL — use a separate DB, e.g. mentor_test)
uv run pytest tests/unit/test_home.py::test_home_marks_active_nav_item   # single test
uv run pytest -m e2e --base-url http://localhost:8000 tests/e2e          # Playwright mobile smoke (server must be running)
uv run pytest -m contract         # real AI provider APIs, opt-in, needs credentials
uv run ruff check . && uv run ruff format --check . && uv run mypy      # same gates as CI (mypy is strict)
uv run alembic upgrade head       # migrations; revision files are named YYYYMMDD_<rev>_<slug>.py
uv run alembic revision --autogenerate --rev-id 0003 -m "..."   # then review: enum drops in downgrade, server defaults
uv run alembic check              # CI fails if models and migrations drift
uv run python -m mentor show EMAIL | set-plan EMAIL tester | set-quota EMAIL job_import {N|unlimited|default}   # admin CLI
```

`addopts` excludes the `contract` and `e2e` markers by default. Passing `-m e2e` or `-m contract` overrides that.

CSS is built with the **Tailwind v4 standalone CLI** (no Node): `tailwindcss -i src/mentor/web/static/css/input.css -o src/mentor/web/static/css/app.css`. `app.css` is git-ignored and built by the Dockerfile `css` stage, the compose `tailwind` service, and CI.

CI (`.github/workflows/ci.yml`) runs these jobs: secrets-scan (gitleaks over full history), lint (ruff + mypy), test (pgvector service; migrations up/down/up, then pytest), e2e (Playwright), and docker build. A deploy workflow comes in Milestone 10.

## Security conventions (D36, D37)
- No credential or secret is ever committed, including dev/CI ones: compose reads them from the git-ignored `.env`, CI's throwaway Postgres uses trust auth, and CI generates a per-run `SECRET_KEY`. gitleaks runs in pre-commit and CI.
- Third-party GitHub Actions are pinned to commit SHAs (with a `# vX.Y.Z` comment). Downloaded binaries (Tailwind, gitleaks) are checksum-verified, and CDN scripts carry SRI `integrity`. Bumping a version means updating its hash too.
- `security.py` sets the CSP and other headers on every response. The CSP has no `'unsafe-inline'`/`'unsafe-eval'`: no inline `<script>`, `style=""` or `hx-on`. htmx is configured to not inject inline styles. A new external origin (e.g. a CDN) must be added to the CSP deliberately. The e2e smoke test fails on any console error, including CSP violations.
- Published compose ports are bound to `127.0.0.1`.

## Architecture

- `src/mentor/main.py`: the `create_app()` factory (run with `uvicorn --factory mentor.main:create_app`) wires middleware, routers and the exception handlers that turn `LoginRequiredError`/`ConsentRequiredError` into redirects and `QuotaExceededError` into a 429 partial. The dev-login router is only included when `APP_ENV` is dev/test.
- `settings.py`: pydantic-settings loaded from env/`.env`. `get_settings()` is cached (tests that change env use the `fresh_settings` fixture). In prod, Google login credentials, `PRIVACY_CONTACT_EMAIL` and the selected AI providers' credentials are required, and `fake` AI adapters are refused. Secrets are `SecretStr` with **no defaults** (`DATABASE_URL`, `SECRET_KEY` ≥32 chars), and `APP_ENV` defaults to `prod`. Use `settings.database_dsn` for the plain URL.
- `db.py`: async SQLAlchemy engine/sessionmaker and declarative `Base`. Models live in each feature package (`auth/models.py`, `quotas/models.py`, `interview/models.py`, `profile/models.py`); register new model modules in `db.import_models()` or Alembic won't see them. Every user-owned table needs `ForeignKey("users.id", ondelete="CASCADE")`, since account deletion relies on it (D21).
- Auth (`auth/`): route signatures pick a dependency alias from `auth/deps.py`: `CurrentUser` (logged in **and** consented, the default for app pages), `AuthenticatedUser` (no consent needed) or `OptionalUser`. Sessions are opaque cookie tokens, stored hashed (`auth/sessions.py`). State-changing UI actions must be `hx-post`/`hx-delete`, so htmx sends the CSRF header that `auth/csrf.py` enforces; a plain `<form method=post>` will get a 403. Redirect with `web/redirects.redirect()` (it handles htmx) and pass user-supplied targets through `safe_next()`.
- Quotas (`quotas/service.py`): call `quotas.check(db, user, kind, amount)` before a costly action and `quotas.record(...)` after it succeeds. Limits per plan are in `PLAN_LIMITS`, overrides in `user.quota_override`.
- Legal (`legal/`): bump `TERMS_VERSION`/`PRIVACY_VERSION` when the text changes in substance; every user is then asked to consent again.
- AI (`ai/`, D45/D46/D55): text and embeddings use **LangChain**. `ai/ports.py` defines the `ChatModels` factory (`chat(effort=...)` → a chat model runnable, `structured(Schema, effort=...)` → a runnable returning the pydantic model), `Effort`, our STT/TTS ports and `AIError` subclasses. Routes take the `AIServices` dependency (`ai/registry.py`; elsewhere `get_ai()`), whose `chat`/`embeddings`/`stt`/`tts` come from `AI_LLM`/`AI_EMBEDDER`/`AI_STT`/`AI_TTS`: `adapters/gemini.py` (`ChatGoogleGenerativeAI` with retries + `with_fallbacks`, normalized `GoogleGenerativeAIEmbeddings`), `adapters/google_speech.py` or `adapters/fake.py`. Tests always run with fakes (forced in `tests/conftest.py`): the `fake_ai` fixture's `fake_ai.chat.script(...)` scripts text/stream replies, `.script_for(Schema, ...)` scripts structured replies per schema, `.calls` records every call (messages, schema, effort). Adding a provider = new adapter + registry branch + contract test in `tests/contract/` (skips without credentials).
- Agents (`agents/`, D54/D56): agents are **LangGraph** graphs. State is checkpointed in Postgres (`agents/checkpoint.py`: `AsyncPostgresSaver` created at app startup in the lifespan (D59: its `setup()` runs `CREATE INDEX CONCURRENTLY`, which would deadlock against a request's open session if run lazily), `Checkpointer` dependency, its tables excluded from Alembic), one thread per interview. Checkpoints don't cascade from `users`: anything deleting user data must also call `delete_threads()`. Integration tests inject an `InMemorySaver` (`checkpointer` fixture).
- Interview (`interview/`, D47/D50/D54/D57): `InterviewEngine` (`engine.py`) owns the lifecycle and our transcript tables and runs the graph in `agent.py` per user turn: `plan` (structured `TurnPlan`, private reasoning) → `speak` (streams the user-facing message from the plan's brief only; never give it the checklist or profile) ∥ `extract` (`ProfilePatch`) → `apply`. The engine forwards only `speak` tokens and yields `ReplyChunk`s then `ReplyDone`/`ReplyFailed` (transport-agnostic, reused by voice in M6). Each user turn gets at most `MAX_REPLY_ATTEMPTS` generations (D58). Routes get the engine through the `Engine` dependency. Prompts live in `prompts.py` (bump `VERSION` on substantive changes); DB helpers in `store.py`; checklist items in `checklist.py`. Routes stream the reply as SSE with heartbeats. Profile facts (`profile/models.py`) carry `source_turn_id` (NULL for facts the user typed); the review/edit screen is `profile/routes.py` + `profile/edit.py` (parsers, validation, `KINDS` registry; one htmx card/form template pair per kind under `templates/profile/items/`), and the account page is `account/routes.py` (D60); `profile/service.py` renders the profile with short refs and applies patches; `profile/skills.py` normalizes skills (D49).
- Logging AI failures: use `mentor.ai.errors.describe(exc)` (types + code locations). Never log provider, LangChain or pydantic exception messages or tracebacks with messages: they can contain the model's output or the user's words (D58).
- Tests: `tests/integration` runs each test in a rolled-back transaction (`db`, `client` fixtures), with login helpers in `tests/integration/helpers.py`. Scope assertions to the test's own user, never to whole-table counts. No inline `style=`, `<script>` or `hx-on` in templates (the CSP blocks them, and a unit test checks).
- `web/`: the Jinja2 environment with `jinja2.ext.i18n`. The UI is **pt-BR only**, but every user-facing string goes through `_()` (D3). Gettext is installed **newstyle**, so a literal `%` in a string must be written `%%`, and values are passed as kwargs: `_("Perfil %(p)s%% completo", p=0)`.
- Templates: **mobile-first, always responsive to desktop (D38)**. Write the phone layout first, then adapt it with `md:`/`lg:` classes; never ship a screen that only works on one size. `base.html` renders both navs from `components/nav.html`: `bottom_nav` (dock + center FAB, `md:hidden`) and `top_nav` (hidden below `md`). Add a nav item to **both** macros. Reusable UI lives in Jinja macros under `templates/components/` (`nav.html`, `cards.html`, `chat.html`); use these instead of re-styling ad hoc. The e2e smoke test runs every page check at 390/768/1440px.
- Design tokens live in the `@theme` block of `static/css/input.css` (palette D4, AA shades D33). `taupe` (#93827F) is decorative only; use `taupe-strong` for muted text. Text on `bg-sage` must be `text-ink`.

### Planned structure (follow it as milestones land)
- The voice pipeline (M6) is browser AudioWorklet → WebSocket → streaming STT → `InterviewEngine` → streaming TTS, with barge-in. Raw audio is never stored.
- CV bullets must cite the source fact IDs, and a verification pass enforces "Model A" (never invent skills or experience).
- Long-running work (job fetch, CV generation, rendering) runs as Procrastinate (Postgres-backed) tasks in a `worker` service, added in Milestone 7.
- Quotas are per plan (`free`/`tester`/`admin`) with per-user overrides. Voice is capped at 30 minutes per user, lifetime, for the free plan.
