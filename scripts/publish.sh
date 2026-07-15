#!/usr/bin/env bash

set -euo pipefail

if ! git diff --quiet || ! git diff --cached --quiet || \
    test -n "$(git ls-files --others --exclude-standard)"; then
    echo "Refusing to publish from a dirty worktree." >&2
    exit 1
fi

uv sync --locked --all-extras --all-groups
uv run ruff check src tests
uv run ruff format --check src tests
uv run basedpyright src
uv run pytest
uv run nox -s tests
uv run sphinx-build -E -W --keep-going -b html docs/source docs/build/html

uv build --clear
uv run twine check dist/*
uv run --isolated --no-project --with dist/*.whl -- python -c "import nshconfig"
uv run --isolated --no-project --with dist/*.tar.gz -- python -c "import nshconfig"
uv publish "$@"
