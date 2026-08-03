# One image, three surfaces. `steward serve`, `steward console` and the seed
# script are the same code with different arguments, so building three images
# would be three chances for them to disagree about a schema.
#
# Named Containerfile because that is what Podman looks for first; `docker
# build -f Containerfile` reads it too.

FROM python:3.13-slim

# uv, because the lockfile is the reproducibility story and pip would ignore it.
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

WORKDIR /app

# Dependencies resolve in their own layer, so editing a Python file does not
# re-download the world on every rebuild.
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-install-project --no-dev

# README.md is not documentation here — hatchling reads it as the package
# readme and the build fails without it.
COPY README.md ./
COPY src/ src/
COPY scripts/ scripts/
RUN uv sync --frozen --no-dev

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    STEWARD_DB=/data/steward.sqlite3

# The three surfaces share one database, and it outlives the containers.
VOLUME /data

# Subcommand only: `serve --person 1`, `console --port 8788`, `memory list`.
ENTRYPOINT ["python", "-m", "steward"]
CMD ["--help"]
