# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

`nshconfig` is a Python configuration management library built on Pydantic v2. It provides draft configs, type registries, singleton patterns, MISSING field support, and code generation tools. Supports Python 3.9+.

## Commands

```bash
# Install all dependencies
uv sync --all-groups

# Run tests
uv run pytest

# Run a single test file
uv run pytest tests/test_registry.py -v

# Run a specific test
uv run pytest tests/test_registry.py::test_name -v

# Type checking
uv run basedpyright src

# Linting
uv run ruff check src

# Multi-version testing (Python × Pydantic matrix)
uv run nox -s tests

# Build and publish
./scripts/publish.sh
```

## Architecture

**Source layout:** `src/nshconfig/` with private implementation in `_src/`.

Public API is re-exported through `__init__.py`. Users import as `import nshconfig as C`.

### Core modules in `_src/`

- **config.py** — `Config` base class extending Pydantic `BaseModel`. Adds draft/finalize workflow, multi-format serialization (JSON, YAML, TOML, Python files), MutableMapping interface for Lightning compatibility, and schema URI injection.
- **registry.py** — `Registry` for dynamic discriminated unions. Subclasses register with `@registry.register`, parent schemas auto-rebuild. Used as `Annotated[BaseType, registry]` in field annotations.
- **missing.py** — `MISSING` sentinel and `AllowMissing[T]` type alias for optional config fields. `validate_no_missing()` for runtime validation.
- **singleton.py** — `Singleton` descriptor and `singleton` decorator for singleton config patterns.
- **export.py** — Code generation CLI (`nshconfig-export`) that generates TypedDicts and JSON schemas from config classes.
- **adapter.py** — `Adapter` for wrapping/transforming configs.
- **invalid.py** — `Invalid` marker type.
- **utils.py** — `deduplicate` and `deduplicate_configs` utilities.

## Code Style Requirements

- **Every Python file MUST start with `from __future__ import annotations`** — enforced by ruff rule FA102/FA100.
- Use `typing_extensions` for type backports (Python 3.9+ support).
- Type checking uses `basedpyright` in standard mode.
- When writing version-specific Pydantic code, check `PYDANTIC_VERSION` for branching.
- Ruff ignores: F722, F821, E731, E741.
