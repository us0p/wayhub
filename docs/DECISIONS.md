# Mentor — Design Decisions Log

Every product, design or technical decision made during development is recorded here.
**Append a new row whenever a decision is made or changed** — never silently edit history;
if a decision is superseded, add a new entry and mark the old one as `Superseded by Dxx`.

Scope reference: [`SCOPE.md`](../SCOPE.md).

## MVP scope (2026-10-08)
1. Google login
2. Conversational onboarding interview (real-time voice or text) that builds the candidate matrix
3. Per-job CV generation (Model A — strict honesty) from link, screenshots, PDF or text, preceded by a fit warning

## Decisions

| # | Date | Decision | Rationale |
|---|------|----------|-----------|
| D1 | 2026-10-08 | Stack: HTMX + Jinja2, Python 3.12 + FastAPI, PostgreSQL 17 + pgvector, Docker Compose | Defined by the user |
| D2 | 2026-10-08 | AI is provider-agnostic through ports/adapters; mobile-first web app | Functional requirements |
| D3 | 2026-10-08 | UI in pt-BR only; strings go through an i18n layer (Babel/gettext) | Niche is São Paulo residents; keeps the door open for more locales |
| D4 | 2026-10-08 | Visual identity from the reference image (rounded cards with large radii, dark hero card, soft tinted cards, bottom nav with raised center "+" FAB, Poppins, flat illustrations). Palette [coolors](https://coolors.co/93827f-f3f9d2-bdc4a7-2f2f2f-92b4a7) replaces the image colors: `#2F2F2F` dark hero/nav, `#92B4A7` primary CTA, `#F3F9D2` tinted cards, `#BDC4A7` borders/chips, `#93827F` muted text. Shades adjusted where needed to meet WCAG AA | User choice |
| D5 | 2026-10-08 | Voice = real-time **cascaded streaming**: browser mic → WebSocket → streaming STT → LLM (streamed) → streaming TTS → browser; barge-in supported; each stage is a swappable adapter | Keeps voice provider-agnostic (native speech-to-speech APIs are vendor-specific) |
| D6 | 2026-10-08 | First AI provider: Google Gemini (chat, structured output, vision, embeddings) | User choice |
| D7 | 2026-10-08 | STT/TTS: Google Cloud Speech-to-Text v2 (Chirp) + Google Cloud Text-to-Speech | Single vendor with Gemini; ~US$0.32–0.46 per 20-min interview; TTS free tier of 1M chars/month (Neural2/Chirp 3 HD). Compared against Deepgram + ElevenLabs (~US$0.57) |
| D8 | 2026-10-08 | Job links: server-side fetch (httpx, then Playwright headless for JS pages) + LLM extraction; when blocked (login wall/captcha/empty) ask the user to paste text or send screenshots | Generic coverage without per-ATS parser upkeep |
| D9 | 2026-10-08 | Job inputs: link, pasted text, multiple screenshots (merged into one job), PDF upload | User choice |
| D10 | 2026-10-08 | CV: one ATS-friendly single-column template, no photo; structured section editing (HTMX inline); PDF (WeasyPrint) and DOCX (python-docx) rendered from the same structured data | Exports always match the editor; ATS parsing |
| D11 | 2026-10-08 | CV language follows the job posting language; user can override | User choice |
| D12 | 2026-10-08 | Model A enforcement: every CV bullet cites its source profile fact(s); a second LLM verification pass removes/rewrites unsupported claims before the user sees the CV | Strict-honesty policy from SCOPE.md |
| D13 | 2026-10-08 | Fit warning in MVP: factual per-requirement gap list (~2y slack + qualitative weighting); never blocks generation | SCOPE.md Phase 1 match heuristic |
| D14 | 2026-10-08 | Interview is checklist-driven (identity/contact, experiences, education, skills, languages, location, work mode, seniority, links…). Agent infers as much as possible from free speech and continues until every item is closed **and** it has no more questions. Resumable across sessions and voice/text; a follow-up interview can add more later | User choice |
| D15 | 2026-10-08 | End of interview → structured review + edit screen; profile remains editable afterwards | Fix STT/LLM mistakes (names, emails, companies) |
| D16 | 2026-10-08 | CV generation unlocked only after the full interview is complete | User choice |
| D17 | 2026-10-08 | Interview guardrails: user can say "enough for now" (gaps flagged, next session resumes), progress indicator (checklist coverage), quotas | Cost and UX |
| D18 | 2026-10-08 | Quotas: plan/role (`free`, `tester`, `admin`) with default limits + per-user override; `tester`/`admin` unlimited; managed through an admin CLI | Quotas must never block testing |
| D19 | 2026-10-08 | pgvector used for (a) skill normalization (canonical skills + aliases) and (b) retrieving experience bullets matching each job requirement (fit + CV) | Groundwork for the Phase 1.5 taxonomy; better fact selection |
| D20 | 2026-10-08 | Hosting: single VPS with Docker Compose + Caddy (automatic HTTPS on the user's domain) | Simple and cheap |
| D21 | 2026-10-08 | LGPD MVP: versioned consent at first login (including third-party AI voice processing), immediate account deletion, no raw audio stored (transcripts only). Data export deferred | Minimum compliance for MVP |
| D22 | 2026-10-08 | Full history: saved jobs (source, parsed job, fit result) with every CV version, reopenable and editable | User choice |
| D23 | 2026-10-08 | Niche (SP + tech) is soft: anyone can use the app; location and job area are recorded; gentle notice when a job is outside tech | Don't block early users; keep metrics |
| D24 | 2026-10-08 | Testing: TDD with pytest + in-memory fake AI adapters; opt-in contract tests against real Google APIs; Playwright mobile smoke tests | Fast, free, deterministic tests |
| D25 | 2026-10-08 | Frontend: Tailwind standalone CLI (no Node toolchain) with palette tokens; HTMX + htmx ws extension; one vanilla JS module for mic capture (AudioWorklet) and playback | Minimal JS, no Node |
| D26 | 2026-10-08 | Voice failure (mic denied/unsupported/bad network) → automatic fallback to text in the same session | UX |
| D27 | 2026-10-08 | Voice UI: live transcript bubbles for both sides + mic level + checklist progress + mute/end | Lets the user catch STT mistakes |
| D28 | 2026-10-08 | Long tasks (fetch, CV generation + verification, rendering) run on a Postgres-backed queue (Procrastinate) in a worker container; UI polls through HTMX | Survives restarts, no Redis |
| D29 | 2026-10-08 | Uploaded screenshots/PDFs are deleted after parsing; only extracted text + parsed job are kept | Data minimization |
| D30 | 2026-10-08 | Own thin asyncio voice pipeline (not Pipecat) so text and voice share the same `InterviewEngine` | Single interview logic for both modes |
| D31 | 2026-10-08 | Voice limit: 30 voice minutes per user, **lifetime** (free-plan default; plan/user overrides apply; tester/admin unlimited). Remaining minutes shown in the voice UI; when exhausted, a UI warning appears and the interview continues text-only | Cost control |
| D32 | 2026-10-08 | GitHub Actions CI/CD. PR + main: ruff lint/format, mypy, pytest (pgvector service), Tailwind build, Docker build, Playwright smoke. Merge to main: build + push image to GHCR → SSH deploy to the VPS (`docker compose pull && up -d`, Alembic migrations) | Enforce quality during development |
| D33 | 2026-10-08 | AA-safe derived shades added to the palette: `#6B5D5A` (taupe-strong) for muted text (6.3:1 on white) and `#4E6E62` (sage-strong) for sage-colored text; `#93827F` is decorative only (3.66:1 fails AA); text on the `#92B4A7` CTA is always `#2F2F2F` (5.93:1; white is 2.26:1) | WCAG AA check of the D4 palette |
| D34 | 2026-10-08 | Tooling pins: Tailwind v4.1.4 (CSS-first `@theme` tokens in `input.css`), htmx 2.0.4 from jsdelivr, uv 0.5.11, Python 3.12. The `worker` compose service is introduced with Procrastinate in Milestone 7, not as an empty placeholder | Reproducible builds; no dead infrastructure |
| D35 | 2026-10-08 | Project renamed from "WayHub" to **Mentor**: Python package `mentor`, DB names, UI title, and docs (including SCOPE.md). The repository directory is still `wayhub/` | User decision |
| D36 | 2026-10-08 | Secrets policy: no credential is committed, **even for dev or CI**. Compose reads `POSTGRES_*`/`SECRET_KEY` from a git-ignored `.env` (fails fast if missing); CI's ephemeral Postgres uses trust auth (no password exists) and `SECRET_KEY` is generated per run; app settings have no secret defaults and use `SecretStr` (kept out of reprs/logs); `APP_ENV` defaults to `prod`. gitleaks runs in pre-commit and as a CI job over the full git history | Security review: credentials were in plain text in `ci.yml` and `docker-compose.yml` |
| D37 | 2026-10-08 | Security hardening baseline: compose ports bound to 127.0.0.1 (Docker-published ports bypass ufw); strict CSP (no `unsafe-inline`/`unsafe-eval`) + nosniff, `X-Frame-Options: DENY`, Referrer-Policy, Permissions-Policy (microphone self-only), COOP; FastAPI docs/OpenAPI disabled; htmx loaded with SRI and `allowEval:false`; GitHub Actions pinned to commit SHAs with a read-only `GITHUB_TOKEN` and `persist-credentials: false`; Tailwind/gitleaks binaries checksum-verified; HSTS set by Caddy (M10) | Security review |
| D38 | 2026-10-08 | Every screen is designed **mobile-first** and must be **responsive down to desktop**: the phone layout is the default, `md:` (≥768px) and `lg:` (≥1024px) adapt it. Below `md` the main navigation is a bottom dock with the raised center "+" FAB; from `md` up it becomes a sticky **top menu** (logo, text links with an active pill, "Nova vaga" CTA on the right; link icons appear from `lg`). Desktop has no phone frame: content widens to `max-w-6xl` on the canvas background, and multi-column layouts are used where they help. Responsive e2e tests cover 390px (iPhone 13), 768px and 1440px | User requirement; extends D2/D4 |
| D39 | 2026-10-08 | Free-plan daily limits: 150 interview turns, 10 job imports, 10 CV generations (each regeneration counts); voice stays 30 min lifetime (D31). Daily windows reset at midnight America/Sao_Paulo | Moderate: real use, abuse capped at a few US$/user/day |
| D40 | 2026-10-08 | Terms of use and privacy policy: clear pt-BR texts covering LGPD basics (data collected, purposes, AI processing by Google as operator, no audio stored, retention, rights and deletion), published as final (not marked as draft). Each document is versioned and consent stores the versions, so a change re-prompts users | User choice |
| D41 | 2026-10-08 | Login sessions: 30-day sliding expiry, opaque random token in an HttpOnly/Secure/SameSite=Lax cookie, only its SHA-256 stored server-side; revocable; all sessions revoked on logout-everywhere and account deletion | User choice |
| D42 | 2026-10-08 | Dev/test login: `/auth/dev-login` (sign in as any email and pick its plan) exists only when `APP_ENV` is `dev` or `test`; the route is not registered in prod (covered by a test). Real Google login works in dev when client credentials are set | User choice; fast local/e2e testing |
| D43 | 2026-10-08 | Added `--color-danger: #9B3328` for errors and destructive actions (7.25:1 on white, 6.66:1 on cream; white text on it 7.25:1). The palette had no error color | Accessible error states |
| D44 | 2026-10-08 | Auth/consent implementation: authlib OIDC with a short-lived signed `mentor_oauth` cookie for state/nonce only; login requires `email_verified`; real logins never change the plan; CSRF = SameSite=Lax + Origin check + session-bound HMAC token sent by htmx (`hx-headers` on `<body>`), so every state-changing UI action uses `hx-post`; consent gate on every app page (`CurrentUser` dependency) with a `next` path validated against open redirects; account deletion requires typing EXCLUIR and hard-deletes through `ON DELETE CASCADE`; Terms/Privacy state 18+ only; app built through `create_app()` (uvicorn `--factory`) | Milestone 2 |
