# CI and local verification (ED-11)

## What CI runs

| Job | Checks |
| --- | --- |
| `unit` | `compileall`, Ruff, pytest (unit + Streamlit admin), Python 3.11 & 3.12 |
| `integration` | Postgres 16 + Redis 7 services; migrations, duplicate claim, Redis locks, concurrent holds, tenant routing |
| `smoke-docs` | Required docs + `requirements/lock.txt` + `render.yaml` exist |

Workflow: `.github/workflows/ci.yml`. JUnit XML lands in Actions artifacts.

## Reproducible installs

```bash
python -m pip install -r requirements/lock.txt
python -m pip install -e ".[dev,admin]"
```

Regenerate the lock after dependency changes:

```bash
uv pip compile pyproject.toml --extra dev --extra admin -o requirements/lock.txt
```

Deploy/install should prefer the lockfile so CI and Render resolve the same versions.

## Local commands

```bash
# default suite (sqlite / memory)
python -m pytest -q -m "not integration and not ai_live"

# lint + syntax
python -m compileall -q src admin e2e tests
python -m ruff check src admin e2e tests

# live integration (Docker examples)
export INTEGRATION_DATABASE_URL=postgresql+asyncpg://fivecent:fivecent@localhost:5432/fivecent_test
export INTEGRATION_REDIS_URL=redis://localhost:6379/0
python -m pytest -q tests/integration -m integration
```

## Markers

- `integration` — needs live Postgres/Redis (`INTEGRATION_*` URLs)
- `ai_live` — optional live LLM evals (not in default CI); scripted contracts live in `tests/evaluation/`

## Release gate

Merge/release should wait for green CI. After deploy, smoke `/live` + `/ready` (see `docs/ops-production.md`). E2E against a running API:

```bash
E2E_API_BASE=http://127.0.0.1:8000 ./e2e/run.sh run pilot_suite
```
