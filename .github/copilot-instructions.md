# nshconfig - AI Coding Agent Instructions

## Project Overview

`nshconfig` is a configuration management library built on top of Pydantic v2. It provides enhanced features for typed configurations including draft configs, registries for dynamic type resolution, singleton patterns, and code generation tools.

## Development Environment

This project uses **UV** for dependency management and packaging (with **uv_build** as the build backend).

### Key Commands

```bash
# Install dependencies (including dev dependencies)
uv sync --all-groups

# Run tests
uv run pytest

# Run tests with coverage
uv run pytest --cov=nshconfig --cov-report=term-missing

# Type checking
uv run basedpyright src

# Linting
uv run ruff check src

# Add a new dependency
uv add <package>           # runtime dependency
uv add --group dev <pkg>   # dev dependency

# Run any command in the UV environment
uv run <command>
```

## Architecture

### Source Layout

- **[src/nshconfig/](src/nshconfig/)** - Main package using src-layout
    - **[\_src/](src/nshconfig/_src/)** - Core implementation modules (private)
    - **[bin/](src/nshconfig/bin/)** - CLI entry points (`nshconfig-export`)
- **[tests/](tests/)** - pytest test suite
- **[docs/](docs/)** - Sphinx documentation

### Core Components

1. **Config** ([\_src/config.py](src/nshconfig/_src/config.py)) - Base class extending Pydantic's `BaseModel` with:
    - Draft configs via `Config.draft()` / `.finalize()`
    - Multi-format serialization (JSON, YAML, TOML, Python files)
    - MutableMapping interface for Lightning compatibility
    - Schema URI injection for JSON/YAML files

2. **Registry** ([\_src/registry.py](src/nshconfig/_src/registry.py)) - Dynamic discriminated unions:
    - Register subclasses with `@registry.register`
    - Auto-rebuild parent schemas when new types are registered
    - Use `Annotated[BaseType, registry]` in field annotations

3. **MISSING** ([\_src/missing.py](src/nshconfig/_src/missing.py)) - Optional field handling:
    - Use `AllowMissing[T]` type alias for optional fields
    - Assign `MISSING` as default value
    - Validate with `validate_no_missing()`

4. **Export/Codegen** ([\_src/export.py](src/nshconfig/_src/export.py)) - Code generation:
    - CLI: `nshconfig-export` generates TypedDicts and JSON schemas
    - Use `Export()` annotation to mark types for export

## Patterns & Conventions

### Config Class Definition

```python
from __future__ import annotations
import nshconfig as C
from typing import Literal

class MyConfig(C.Config):
    name: str
    value: int
    optional_field: C.AllowMissing[float] = C.MISSING
```

### Registry Pattern for Plugins

```python
from abc import ABC, abstractmethod
from typing import Annotated, Literal

class PluginBase(C.Config, ABC):
    type: str

registry = C.Registry(PluginBase, discriminator="type")

@registry.register
class MyPlugin(PluginBase):
    type: Literal["my_plugin"] = "my_plugin"

class AppConfig(C.Config):
    plugin: Annotated[PluginBase, registry]
```

### Important Code Style

- All Python files MUST start with `from __future__ import annotations`
- Use `typing_extensions` for backports (project supports Python 3.9+)
- Pydantic v2 compatibility: check `PYDANTIC_VERSION` for version-specific code paths
- Type checking with `basedpyright` in standard mode

## Testing

Tests use pytest with fixtures in [conftest.py](tests/conftest.py). Run specific tests:

```bash
uv run pytest tests/test_registry.py -v  # Registry tests
uv run pytest tests/test_config.py -v    # Config serialization tests
```

Multi-version testing via nox (tests against multiple Python + Pydantic versions):

```bash
uv run nox -s tests
```

## Common Tasks

### Adding New Config Features

1. Add implementation to appropriate `_src/*.py` module
2. Export from `__init__.py` if public API
3. Add tests in `tests/test_*.py`
4. Update docs if user-facing

### Modifying Serialization

The Config class supports JSON, YAML, TOML, and Python file formats. Format-specific code is in [\_src/config.py](src/nshconfig/_src/config.py) methods like `to_json_str()`, `from_yaml()`, etc.
