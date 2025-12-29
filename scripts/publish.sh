#!/bin/bash

# Publish script for nshconfig package
set -e
set -u

# 0. Make sure all dependencies are installed
uv sync --all-extras --all-groups

# 1. Run checks (loading .env if your tests need it)
uv run --env-file .env ruff check
uv run --env-file .env basedpyright
uv run --env-file .env pytest

# 2. Build and Publish
uv build
uv run --env-file .env uv publish $@