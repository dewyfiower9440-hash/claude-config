---
paths:
  - "**/*.{py,pyi}"
  - "**/pyproject.toml"
---

# Python rules (applies to *.py)

## Project & tooling
- `uv` only: `uv init --package`, `uv add <pkg>`, `uv add --dev <pkg>`, `uv run <cmd>`. Never `pip install` or global installs; commit `uv.lock`.
- Target the project's `requires-python`; default Python 3.12+ syntax (`X | None`, `list[int]`, `match`, `type` aliases on 3.12+).
- src layout: `src/<pkg>/`, tests in `tests/` mirroring the package. Entry points via `[project.scripts]`.
- Quality gate: `uv run ruff check . && uv run ruff format --check . && uv run pyright && uv run pytest -q`.

## Types
- Full annotations on every function signature, including `-> None`. pyright strict must stay clean.
- No `Any` unless at an untyped third-party boundary, and then narrow immediately. Use `TypedDict`/`Protocol`/`dataclass`/pydantic models instead of loose dicts.
- Domain values: `@dataclass(frozen=True, slots=True)` for internal value objects; pydantic `BaseModel` for boundary parsing (HTTP, files, env, LLM output). Use `Literal`/`Enum` for closed sets.
- Interfaces for adapters: `typing.Protocol`. Inject them; don't import concrete adapters in the core.

## Structure
- `domain/` (pure logic, models, errors), `adapters/` (db, http, fs, clock), `app/` or `services/` (use cases that orchestrate), `cli.py`/`main.py` (composition root). Small projects may flatten this, but keep I/O out of pure logic.
- Config: `pydantic-settings` `Settings` loaded once in the composition root; pass values down, don't read `os.environ` in the core.

## Errors
- Base `class AppError(Exception)` per project; subclasses carry context fields. `raise DomainError(...) from e` when wrapping.
- Never bare `except:` or `except Exception: pass`. Catch `Exception` only at top-level boundaries (CLI main, request handler, job runner), where it is logged with the traceback and converted to an exit code or response.
- `httpx` with explicit `timeout=`; retries via `tenacity` (bounded attempts, exponential backoff + jitter, idempotent ops only).

## Logging
- `structlog` configured once (JSON in prod, console in dev) with a redaction processor for keys like `password|token|secret|authorization|connection_string`. `log = structlog.get_logger(__name__)`.
- Event names `noun.verb` (`user.created`) + key-value context. Use `log.exception(...)` inside `except` to keep the traceback.

## Async
- Don't mix sync blocking I/O into async code (`requests`, `time.sleep`, blocking DB drivers). Use `httpx.AsyncClient`, `asyncio.sleep`, `asyncio.to_thread` for unavoidable blocking calls.
- Structured concurrency: `asyncio.TaskGroup`; every task is awaited or owned. Timeouts via `asyncio.timeout()`.

## Testing
- pytest, plain `assert`, fixtures over setup methods, `tmp_path` for files, `monkeypatch` for env. `pytest.mark.parametrize` for input tables.
- Property-based (`hypothesis`) for parsers, serializers and invariants-heavy logic.
- Coverage: `uv run pytest --cov=src --cov-report=term-missing`.

## Common mistakes to avoid
- Mutable default arguments; module-level side effects at import time; `datetime.now()` without tz (use `datetime.now(UTC)` and inject a clock in the core); `open()` without `encoding="utf-8"` (Windows defaults to cp949); building SQL with f-strings (use parameters); `subprocess` with `shell=True` or unvalidated input; relying on dict ordering of external JSON; catching and re-raising without `from`.
- Paths: `pathlib.Path`, never string concatenation; remember Windows `\\` vs `/` in tests.
