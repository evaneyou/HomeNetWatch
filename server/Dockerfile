# Base images are pinned by digest; Dependabot (docker) proposes new digests.
FROM python:3.15-slim@sha256:e97b88e416c3c01eb427b21fd8cd3ebf006e6ee38afcfcc3cae1c54ee2ab0a75

COPY --from=ghcr.io/astral-sh/uv:0.13.1@sha256:4a1f3666816dafadd20ceb71eac9a7d56d7ef30279f7d64048519ab901ada336 /uv /usr/local/bin/uv

WORKDIR /app

# The containers run read-only, so compile bytecode at build time and never try to write it later.
ENV UV_COMPILE_BYTECODE=1 PYTHONDONTWRITEBYTECODE=1

# Install exactly what uv.lock lists, checked against its hashes, so production runs what CI tested.
COPY pyproject.toml uv.lock README.md ./
RUN uv export --frozen --no-dev --no-emit-project --format requirements-txt -o /tmp/requirements.txt \
    && uv pip install --system --require-hashes -r /tmp/requirements.txt \
    && rm /tmp/requirements.txt

COPY app ./app
COPY alembic ./alembic
COPY alembic.ini ./
COPY tests ./tests

RUN uv pip install --system --no-deps .

# The API runs as this user, not root (uid 10001 matches no account on the host). The shared state volumes are
# handed to it by the state-init service in docker-compose.yml. hoststat and state-init override the user.
RUN groupadd --system --gid 10001 hns \
    && useradd --system --uid 10001 --gid 10001 --no-create-home --home-dir /nonexistent --shell /usr/sbin/nologin hns
ENV HOME=/tmp
USER 10001:10001

EXPOSE 8847

CMD ["sh", "-c", "alembic upgrade head && uvicorn app.main:app --host 0.0.0.0 --port 8847"]
