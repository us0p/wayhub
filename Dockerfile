# syntax=docker/dockerfile:1.7
ARG PYTHON_VERSION=3.12
ARG TAILWIND_VERSION=v4.1.4
# sha256 of tailwindcss-linux-x64 from the release's sha256sums.txt — update together with the version
ARG TAILWIND_SHA256=0fa8bbb8beca7242f6455bf01bbe35d3083878f6308ec4a4cec30aed16bca46d

# --- CSS build (Tailwind standalone CLI, no Node — D25) ---
FROM debian:bookworm-slim AS css
ARG TAILWIND_VERSION
ARG TAILWIND_SHA256
ADD --checksum=sha256:${TAILWIND_SHA256} \
    https://github.com/tailwindlabs/tailwindcss/releases/download/${TAILWIND_VERSION}/tailwindcss-linux-x64 \
    /usr/local/bin/tailwindcss
RUN chmod +x /usr/local/bin/tailwindcss
WORKDIR /build
COPY src/mentor/web ./src/mentor/web
RUN tailwindcss -i src/mentor/web/static/css/input.css -o src/mentor/web/static/css/app.css --minify

# --- Python app ---
FROM python:${PYTHON_VERSION}-slim AS app
COPY --from=ghcr.io/astral-sh/uv:0.5.11 /uv /uvx /bin/
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PROJECT_ENVIRONMENT=/opt/venv \
    PATH="/opt/venv/bin:$PATH" PYTHONUNBUFFERED=1
WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv uv sync --frozen --no-dev --no-install-project
COPY . .
COPY --from=css /build/src/mentor/web/static/css/app.css src/mentor/web/static/css/app.css
RUN --mount=type=cache,target=/root/.cache/uv uv sync --frozen --no-dev
RUN useradd --create-home --uid 1000 mentor
USER mentor
EXPOSE 8000
CMD ["uvicorn", "--factory", "mentor.main:create_app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers"]

# --- Dev image: includes dev dependencies ---
FROM app AS dev
USER root
RUN --mount=type=cache,target=/root/.cache/uv uv sync --frozen
USER mentor
