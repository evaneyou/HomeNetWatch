#!/usr/bin/env bash
# Run the server tests in a throwaway container against a separate hns_test database,
# so the live hns database is never migrated or seeded by a test run.
set -euo pipefail
cd "$(dirname "$0")/.."

db_container=$(docker compose ps -q db)
user=$(docker exec "$db_container" printenv POSTGRES_USER)
password=$(docker exec "$db_container" printenv POSTGRES_PASSWORD)
network=$(docker inspect -f '{{range $name, $_ := .NetworkSettings.Networks}}{{$name}}{{end}}' "$db_container")

if ! docker exec "$db_container" psql -U "$user" -d postgres -tAc "SELECT 1 FROM pg_database WHERE datname = 'hns_test'" | grep -q 1; then
  docker exec "$db_container" psql -U "$user" -d postgres -c "CREATE DATABASE hns_test"
fi

docker build -q -t home-sensor-server-test ./server >/dev/null
docker run --rm --network "$network" \
  -v "$PWD/server:/src" -v "$PWD/monitor:/monitor:ro" -w /src \
  -e PYTHONPATH=/src \
  -e DATABASE_URL="postgresql+psycopg://$user:$password@db:5432/hns_test" \
  -e SECRET_KEY=test-secret \
  -e BOOTSTRAP_ADMIN_PASSWORD=admin-change-me \
  -e BOOTSTRAP_VIEWER_PASSWORD=viewer-change-me \
  home-sensor-server-test \
  sh -c "pip install -q --root-user-action=ignore pytest pytest-asyncio >/dev/null &&
         alembic upgrade head >/dev/null &&
         python -m app.seed >/dev/null &&
         python -m pytest -q -p no:cacheprovider $*"
