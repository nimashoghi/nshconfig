# nshconfig v2

Typed, provenance-aware configuration for ML runs (drafts + `interp()` interpolation +
`finalize()` + `explain()`), on pydantic >= 2.13 and Python >= 3.10. Usage guide for
agents: `SKILL.md`.

## Commands

```bash
# Install all dependencies
uv sync --all-groups

# Run tests
uv run pytest

# Run a single test file
uv run pytest tests/test_interp.py -v

# Type checking (src must stay at 0 errors, 0 warnings)
uv run basedpyright src

# Linting
uv run ruff check src tests

# Multi-version testing (Python x pydantic floor/latest)
uv run nox -s tests

# Docs
uv run sphinx-build -b html docs/source docs/build/html

# Build and publish
./scripts/publish.sh
```

## Conventions

- No `from __future__ import annotations` anywhere: PEP 563 is a legacy path (PEP 649 lazy
  annotations are the 3.14 default), eager annotations match notebook-defined classes under
  cloudpickle, and the 3.10 floor makes the syntax motivation moot. Quote forward references
  explicitly when a name is defined later.
- The golden typing probes in `tests/typing_probes/` are part of the contract: changes that
  alter what basedpyright reports there are behavior changes, not flakes.
- The transport tests spawn subprocesses (cloudpickle canaries); they are required, not slow
  extras.
