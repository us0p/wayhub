# Mentor MVP — Implementation Plan


> Approved plan for the MVP (2026-10-08). **`docs/DECISIONS.md` is the source of truth for
> decisions**: the table below is the snapshot at approval time (D1–D32); later decisions
> (D33+, e.g. the rename to Mentor, the security baseline and responsive layout) live only there.
> Update the milestone status below as work lands.

## Status
| Milestone | Status |
|---|---|
| 1. Scaffold | Done — branch `feat/m1-scaffold` |
| 2. Auth + consent + quotas | Done — PR #1 (`feat/m2-auth-consent-quotas`) |
| 3–10 | Not started |

## Context
Mentor Phase 1 (see `SCOPE.md`) is a candidate-focused career tool. This MVP delivers:
1. **Google login**
2. **Conversational onboarding interview** (real-time voice or text) that builds the candidate's
   matrix of experiences, education, skills, languages and preferences
3. **Per-job CV generation** (Model A — strict honesty) from a job given as link, screenshots,
   PDF or pasted text, preceded by a factual **fit warning**

The repo is greenfield (only `SCOPE.md`). Stack: HTMX + FastAPI + PostgreSQL/pgvector + Docker.
AI must be provider-agnostic; the app is mobile-first, pt-BR, styled after the reference image
using the user's palette.

---

## Decisions log
Copy verbatim to `docs/DECISIONS.md` as the first implementation step. **Every new decision
made during development gets appended there** (rule also written into CLAUDE.md).

| # | Decision |
|---|----------|
| D1 | Stack: HTMX + Jinja2, Python 3.12 + FastAPI, PostgreSQL 17 + pgvector, Docker Compose |
| D2 | AI provider-agnostic through ports/adapters; mobile-first web app |
| D3 | UI pt-BR only; strings via an i18n layer (Babel/gettext) so more locales can be added later |
| D4 | Visual identity from reference image (rounded cards with large radii, dark hero card, soft tinted cards, bottom nav with raised center "+" FAB, Poppins, flat illustrations). Palette replaces image colors: `#2F2F2F` dark hero/nav, `#92B4A7` primary CTA, `#F3F9D2` tinted cards, `#BDC4A7` borders/chips, `#93827F` muted text; tokens adjusted to meet WCAG AA |
| D5 | Voice = real-time **cascaded streaming**: browser mic → WebSocket → streaming STT → LLM (streamed) → streaming TTS → browser; barge-in supported; every stage is a swappable adapter |
| D6 | First AI provider: **Google Gemini** (chat, structured output, vision, embeddings) |
| D7 | STT/TTS: **Google Cloud Speech-to-Text v2 (Chirp) + Google Cloud TTS** (same vendor as Gemini; ~US$0.32–0.46 per 20-min interview) |
| D8 | Job link: server-side fetch (httpx, then Playwright headless for JS pages) + LLM extraction; when blocked (login wall/captcha/empty), ask for paste or screenshots |
| D9 | Job inputs: link, pasted text, **multiple screenshots** (merged into one job), **PDF upload** |
| D10 | CV: one ATS-friendly single-column template, no photo; structured section editing (HTMX inline); PDF (WeasyPrint) + DOCX (python-docx) rendered from the same structured data |
| D11 | CV language follows the job posting language; user can override |
| D12 | Model A enforcement: every CV bullet must cite the source profile fact(s); a second LLM verification pass removes or rewrites unsupported claims before the user sees the CV |
| D13 | Fit warning in MVP: factual per-requirement gap list (~2y slack + qualitative weighting); never blocks generation |
| D14 | Interview is checklist-driven (identity/contact, experiences, education, skills, languages, location, work mode, seniority, links…). The agent infers as much as possible from free speech and continues until all items are closed **and** it has no more questions. Resumable across sessions and across voice/text; a follow-up interview can add more later |
| D15 | End of interview → structured **review + edit** screen; profile stays editable afterwards |
| D16 | CV generation is unlocked **only after the full interview is complete** |
| D17 | Interview guardrails: user can say "enough for now" (gaps flagged, next session resumes), progress indicator (checklist coverage), daily quota |
| D18 | Quotas: plan/role (`free`, `tester`, `admin`) with default daily limits + per-user override; `tester`/`admin` unlimited so tests are never blocked; changed via admin CLI |
| D19 | pgvector used for (a) skill normalization (canonical skills + aliases) and (b) retrieving experience bullets that match each job requirement (fit + CV) |
| D20 | Hosting: single VPS with Docker Compose + Caddy (automatic HTTPS on the user's domain) |
| D21 | LGPD MVP: versioned consent at first login (incl. third-party AI voice processing), immediate account deletion, **no raw audio stored** (transcripts only). Data export deferred |
| D22 | Full history: saved jobs (source, parsed job, fit result) with every CV version, reopenable and editable |
| D23 | Niche (SP + tech) is soft: anyone may use the app; location and job area are recorded; gentle notice when a job is outside tech |
| D24 | Testing: TDD with pytest + in-memory fake adapters; opt-in contract tests against real Google APIs; Playwright mobile smoke tests |
| D25 | Frontend: Tailwind standalone CLI (no Node) with palette tokens; HTMX + htmx ws extension; one vanilla JS module for mic capture (AudioWorklet) and audio playback |
| D26 | Voice failure (mic denied / unsupported / bad network) → automatic fallback to text in the same session |
| D27 | Voice UI: live transcript bubbles for both sides + mic level + checklist progress + mute/end |
| D28 | Long tasks (fetch, CV gen + verification, render) run on a Postgres-backed queue (Procrastinate) in a worker container; UI polls status through HTMX |
| D29 | Uploaded screenshots/PDFs are deleted after parsing; only extracted text + parsed job are kept |
| D30 | Own thin asyncio voice pipeline (not Pipecat) so text and voice share the same `InterviewEngine` |
| D31 | Voice limit: **30 voice minutes per user, lifetime** (free-plan default; plan/user override applies, testers/admins unlimited). Remaining minutes shown in the voice UI; when they run out, a UI warning appears and the interview continues text-only |
| D32 | GitHub Actions CI/CD. PR + main: ruff lint/format, mypy, pytest (pgvector service container), Tailwind build, Docker build, Playwright smoke test. Merge to main: build + push image to GHCR → SSH deploy to VPS (`docker compose pull && up -d`, run Alembic migrations) |

---

## Architecture

```
./
  docker-compose.yml          # app, worker, db (pgvector/pgvector:pg17); compose.prod.yml adds caddy
  Dockerfile                  # python:3.12-slim + WeasyPrint deps; worker target adds Playwright chromium
  pyproject.toml              # uv; fastapi, sqlalchemy[asyncio], asyncpg, alembic, pgvector, authlib,
                              # procrastinate, google-genai, google-cloud-speech, google-cloud-texttospeech,
                              # httpx, playwright, trafilatura, pypdf, weasyprint, python-docx, babel
  docs/DECISIONS.md
  CLAUDE.md                   # via /init after scaffold
  src/mentor/
    main.py  settings.py  db.py  i18n/  cli.py (admin: set-plan, set-quota)
    ai/ ports.py              # LLM, STT, TTS, Embedder, Vision protocols + DTOs
        registry.py           # builds adapters from settings (AI_LLM=gemini, AI_STT=google, ...)
        adapters/gemini.py google_stt.py google_tts.py fake.py
        prompts/              # versioned prompt templates (pt-BR)
    auth/       # Google OIDC (Authlib), server-side sessions, CSRF, consent
    quotas/     # plans, overrides, usage_events, `require_quota()` dependency
    profile/    # models, review/edit views, skill normalization
    interview/  # checklist, InterviewEngine, text routes
    voice/      # WS endpoint, VoicePipeline (STT→engine→TTS), barge-in
    jobs/       # ingestion (url/text/images/pdf), extraction, SSRF-safe fetcher
    fit/        # requirement matching + fit report
    cv/         # generation, verification, editor views, renderers (html/pdf/docx)
    tasks.py    # Procrastinate app + task definitions
    web/templates/ static/ (tailwind input.css, voice.js, audio-worklet.js, illustrations)
  tests/ unit/ integration/ contract/ (marked, opt-in) e2e/ (Playwright)
```

### AI ports (`ai/ports.py`)
- `LLM.generate(messages, *, schema=None) -> Result` and `LLM.stream(messages) -> AsyncIterator[str]`
- `Vision` implemented by the LLM adapter (multi-image + prompt → structured output)
- `Embedder.embed(texts) -> list[vector]` (dimension stored in settings; one model per DB)
- `STT.stream(audio_chunks, lang="pt-BR") -> AsyncIterator[Transcript(text, is_final)]`
- `TTS.stream(text_chunks, voice) -> AsyncIterator[bytes]`
- Every call records usage (tokens / seconds / chars) through `quotas.record()`.
- `fake.py` provides scripted, deterministic implementations for tests.
- Adding a provider = new adapter file + registry entry; no domain code changes.

### Data model (SQLAlchemy + Alembic)
- `users` (google_sub, email, name, plan, quota_override JSONB, location, deleted_at), `sessions`, `consents` (version, accepted_at)
- `usage_events` (user, kind, amount, at) → daily sums against plan/override limits
- `interviews` (status: in_progress/paused/complete, mode), `interview_turns` (role, text, mode, created_at)
- `interview_checklist` (interview, item, status: missing/partial/done, notes)
- Profile facts, each carrying `source_turn_id` for provenance:
  `experiences` (company, title, start/end, is_current, employment_type, technical bool),
  `experience_bullets` (text, embedding vector), `education`, `languages`, `links`,
  `preferences` (work_mode, locations, seniority, target_areas),
  `user_skills` (raw_name, skill_id, years, level, evidence bullet ids)
- `skills` (canonical name, aliases[], embedding, status: approved/uncategorized)
- `jobs` (source_type, source_url, raw_text, parsed JSONB: title, company, seniority, location,
  work_mode, language, area, requirements[], status), `job_requirements` (text, kind must/nice, skill_id, min_years, embedding)
- `fit_reports` (job, per-requirement verdict + factual message, overall)
- `cvs` (job, version, language, content JSONB with bullets → source fact ids, verification log, status)

### Interview engine (shared by text and voice)
Each user turn:
1. **Reply** (streamed): system prompt + profile summary + open checklist gaps + recent turns →
   next question/acknowledgement in pt-BR. The agent never asks about something already known.
2. **Extraction** (in parallel, structured output): turn + context → profile patch
   (upserts with provenance) + checklist status updates. Skills go through normalization.
3. Completion when all checklist items are `done` and the reply model returns `no_more_questions=true`
   → interview `complete` → redirect to review/edit screen (D15). "Chega por agora" → `paused`.
- The text UI is HTMX: POST the turn → server-sent events stream the reply into a bubble; the
  checklist sidebar/progress bar updates through an out-of-band swap.

### Voice pipeline
- `voice.js`: getUserMedia → AudioWorklet → 16 kHz PCM16 frames → WS binary. Playback through an
  AudioContext queue; the server sends JSON control messages (`transcript`, `agent_text`, `stop_audio`, `checklist`, `fallback_text`).
- Server: STT stream (Google v2 streaming; rotated before Google's ~5-min stream limit) → on a final
  utterance → `InterviewEngine` reply stream → sentence chunker → TTS stream → WS.
- **Barge-in**: interim STT text while the agent is speaking → cancel the LLM/TTS tasks and send `stop_audio`.
- No audio is persisted (D21); only final transcripts are saved as turns.
- Voice minutes are counted from streamed audio time and checked against the user's **lifetime** voice
  allowance (D31: 30 min default). The UI shows the minutes left; when they run out mid-session the server
  sends `voice_limit_reached` → warning banner → text composer in the same interview.
- Failure → `fallback_text` message, and the UI swaps to the text composer in the same interview (D26).

### Job ingestion
- **URL**: SSRF guard (http/https only, resolve the hostname and block private/loopback IPs, size and
  time limits, limited redirects) → httpx → trafilatura main text → if thin/JS-only → Playwright → if
  login wall or captcha is detected or the text is too short → status `needs_manual` with a "paste the text or
  send screenshots" prompt.
- **Screenshots** (≤5 images, ≤10 MB each) → one multi-image Vision call. **PDF** → pypdf text,
  falling back to Vision when scanned. Raw files are deleted afterwards (D29).
- **Extraction**: LLM structured output → parsed job + requirements (must/nice, skill, min_years,
  language, area). Page text is wrapped as untrusted data (prompt-injection mitigation); non-job
  content → "isso não parece uma vaga". Non-tech area → soft notice (D23).

### Fit warning
- Deterministic Python: years per skill computed from experience date ranges (overlaps merged).
- Per requirement: pgvector top-k bullets → LLM judge with rules (~2y slack, responsibility/impact
  weighting, non-technical experience transposition) → `met / partial / gap` + factual pt-BR message
  ("a vaga pede 5 anos de Python, você declarou 3").
- Shown before generation, with a "Gerar CV mesmo assim" button (D13).

### CV generation (Model A)
1. Select relevant facts (pgvector per requirement + preferences), omit irrelevant ones.
2. Generator → structured CV JSON in the job's language; every bullet carries `source_ids`.
3. Verifier → checks each claim against the cited facts; unsupported → rewritten or dropped; logged.
4. Editor: inline HTMX editing per field and bullet, reorder/hide, regenerate a section. User edits are
   the user's own statements (stored as `user_edited`).
5. Render: Jinja HTML → WeasyPrint PDF; python-docx DOCX from the same JSON. New version on each regeneration.

### Auth, quotas, LGPD
- Authlib Google OIDC; server-side session cookie (HttpOnly, Secure, SameSite=Lax); CSRF token
  sent through `hx-headers`; WS authenticated by session cookie + origin check.
- First login → consent screen (terms, privacy, AI/voice processing) before any feature.
- `require_quota(kind)` dependency on interview turns, voice minutes, job ingestion, CV generation.
  Over the limit → friendly pt-BR message with reset time.
- Delete account: hard-delete the user and all cascades + revoke the session; the Google token is not stored beyond login.

### Design system
- Tailwind config tokens from D4 (+ computed AA-safe text shades); Poppins via Google Fonts.
- Components (Jinja macros): `hero_card` (dark, with progress ring like the image's 80%),
  `tint_card`, `chip`, `primary_button` (pill), `bottom_nav` (Início · Entrevista · [+ Nova vaga] · Vagas & CVs · Perfil),
  `chat_bubble`, `checklist_progress`.
- Flat illustrations from an open-license set recolored to the palette.
- Mobile-first breakpoints; the desktop layout centers the mobile column, widened for the CV editor.

---

## Milestones (each TDD, each ends with DECISIONS.md updated)
1. **Scaffold**: repo layout, Docker Compose (app/worker/db), settings, Alembic, Tailwind CLI, base
   layout + design tokens + bottom nav, health check, pytest, ruff/mypy config, pre-commit, and
  `.github/workflows/ci.yml` (quality gates, D32). Write `docs/DECISIONS.md`, then **run /init** to create CLAUDE.md (incl. the rule "update DECISIONS.md on every decision").
2. **Auth + consent + quotas** (Google OIDC, sessions, CSRF, plans/overrides, admin CLI, account deletion).
3. **AI ports + Gemini/Google adapters + fakes** (+ contract tests).
4. **Text interview**: checklist, engine, extraction, skill normalization (pgvector), resume/pause, progress.
5. **Review/edit profile screen.**
6. **Voice interview**: WS pipeline, STT/TTS adapters, barge-in, fallback, live transcript UI.
7. **Job ingestion** (text → PDF → screenshots → URL/Playwright + SSRF guard) on the Procrastinate worker.
8. **Fit warning.**
9. **CV generation + verification + editor + PDF/DOCX + history.**
10. **Prod deploy**: compose.prod.yml + Caddy on the VPS/domain, Google OAuth prod redirect, backups (pg_dump cron),
    `.github/workflows/deploy.yml` (GHCR push + SSH deploy + migrations; secrets: `VPS_HOST`, `VPS_USER`,
    `VPS_SSH_KEY`, app env on the VPS only). Recommend branch protection on `main` requiring CI.

## Verification
- `docker compose run --rm app pytest` — unit + integration with fake adapters (DB via the compose Postgres).
- `pytest -m contract` with real Google credentials — adapter contract tests.
- Playwright (iPhone viewport) e2e: login (with a test OIDC stub in dev) → consent → text interview to
  completion with the fake LLM → review → paste job → fit warning → CV → download PDF/DOCX.
- Manual: real voice interview on a phone over HTTPS (mic permission, barge-in, deny mic → text fallback);
  job URLs from Gupy, Greenhouse, a LinkedIn URL (expect manual fallback); open the PDF in an ATS parser check.
- Model A check: seeded profile without skill X + job requiring X → assert X never appears as experience in the CV.
- Quota: a `free` user hits the limit; a `tester` never does. Voice: a `free` user with 29.5 min used
  gets the `voice_limit_reached` warning and continues in text.
- CI: open a PR → all jobs green; a deliberate lint/test failure blocks it. CD: merge → new image on the VPS, `/health` OK.
