# Contributing

Thanks for your interest in HomeNetWatch. Bug reports, ideas, documentation fixes and pull
requests are all welcome.

By taking part you agree to follow the [Code of Conduct](./CODE_OF_CONDUCT.md). Report security
problems privately, as described in [SECURITY.md](./SECURITY.md).

## Before you start

- Read [`SPEC.md`](./SPEC.md). It is the source of truth for architecture, API shape and phases.
- For anything larger than a small fix, open an issue first so we can agree on the approach.
- Check the "Out of scope" section of the spec. Some features are deliberately deferred.

## Development setup

You need Docker (with Compose v2), plus [uv](https://docs.astral.sh/uv/) and Node.js 22 if you work outside containers.

```bash
cp .env.example .env
docker compose up -d db              # Postgres on 127.0.0.1:55432

# Server
cd server
uv sync --extra dev
uv run alembic upgrade head
uv run python -m app.seed
uv run uvicorn app.main:app --reload --port 8847

# Dashboard (new terminal)
cd dashboard
npm ci
npm run dev
```

The dashboard is at <http://127.0.0.1:3847> and the API health check is at
<http://127.0.0.1:8847/api/v1/healthz>. See the [README](./README.md) for the full stack.

## Tests and checks

Run these before you open a pull request. CI runs the same checks.

```bash
# Server tests (needs Postgres from the step above, with the schema migrated and seeded)
cd server && uv run pytest -q

# Or run them in a throwaway container against a separate hns_test database
./scripts/test-server.sh

# Dashboard type-check and production build
cd dashboard && npm run build
```

Add or update tests for any behavior you change. Server tests live in `server/tests/`.

## Pull requests

1. Fork the repository and branch from `main`. Use a short name such as `fix/ingest-duplicate-id`.
2. Keep each pull request focused on one change.
3. Update `README.md`, `SPEC.md`, or the relevant component README when behavior changes.
4. Add a line under **Unreleased** in [`CHANGELOG.md`](./CHANGELOG.md) for user-visible changes.
5. Fill in the pull request template and link the issue it addresses.

### Commit messages

Use [Conventional Commits](https://www.conventionalcommits.org/): `feat:`, `fix:`, `docs:`,
`test:`, `refactor:`, `chore:`. Keep the subject under about 72 characters and write it in the
imperative ("add retention job", not "added retention job").

### Database migrations

Schema changes need an Alembic migration in `server/alembic/versions/`, numbered in sequence.
Migrations must apply cleanly on top of the previous head.

## Code style

- **Python:** 3.12+, type hints on public functions, small modules, no new dependencies without a reason.
- **TypeScript/React:** function components and hooks, TanStack Query for server state, Recharts for charts.
- **Shell:** POSIX `sh` or `bash` with `set -eu`; files use LF line endings (enforced by `.gitattributes`).
- Follow `.editorconfig`.

## Versioning and releases

The project follows [Semantic Versioning](https://semver.org/). The version lives in
[`VERSION`](./VERSION). Maintainers cut a release with:

```bash
./scripts/bump-version.sh 0.3       # updates VERSION, server/pyproject.toml, dashboard/package.json
# move the Unreleased entries in CHANGELOG.md under the new version heading
git commit -am "chore: release v0.3"
git tag v0.3
```

The Pi agent in `monitor/` carries its own `AGENT_VERSION` in `monitor/agent_state.py`. Bump it when
you change the agent.

## License

By contributing you agree that your contributions are licensed under the [MIT License](./LICENSE).
