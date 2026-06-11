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
