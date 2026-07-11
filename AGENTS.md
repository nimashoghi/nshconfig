# nshconfig v2

`nshconfig` is a small lifecycle layer over Pydantic for typed ML-run
configuration. It supports Python 3.10 through 3.14 and Pydantic 2.13 through
the latest Pydantic 2.x release.

Read `DESIGN.md` before changing behavior. It is the semantic authority.
`README.md` is the user introduction, and `SKILL.md` is the concise usage guide
for coding agents.

## Development commands

```bash
# Reproduce the locked development environment, including optional features.
uv sync --locked --all-groups --all-extras

# Tests and a focused test file.
uv run pytest
uv run pytest tests/test_interp.py -v

# Static checks. src must remain at 0 basedpyright errors and warnings.
uv run basedpyright src
uv run ruff check src tests
uv run ruff format --check src tests

# Python 3.10-3.14 against the Pydantic floor and latest 2.x.
uv run nox -s tests

# Documentation and distributions.
uv run sphinx-build -W --keep-going -b html docs/source docs/build/html
uv build --clear
```

Use `./scripts/publish.sh` only for an intentional release.

## Architecture

- Pydantic owns schemas, aliases, validation, serialization, and JSON Schema.
  Import its authoring APIs directly from `pydantic`.
- `nshconfig` adds four distinct states: a `Config` class, a mutable incomplete
  draft, a validated field-frozen final, and an inert JSON run record.
- Calling a `Config` class creates a final. Create composition state only with
  `draft(ConfigType)` and cross the validation boundary with the non-destructive
  `finalize()` function.
- Interpolation is a whole-field Python callable. Field declaration order is
  dependency order, and interpolation may read only canonical values whose
  complete field validation has already finished.
- Draft writes, deletion, built-in container mutation, and interpolation produce
  provenance. `explain()` follows interpolation dependencies.
- Finals are shallowly field-frozen and all `Config` instances are unhashable.
  Use `fingerprint()` for deterministic content identity.
- Run records contain concrete JSON-safe values and provenance. Loading one must
  never execute interpolation. Cloudpickle is separate, trusted, short-lived
  executable transport.
- Structural `Config` positions must have concrete annotations. Drafts and
  interpolation markers may not be hidden under `Any`, `object`, or opaque
  values.
- The public API is the narrow function-based surface in
  `src/nshconfig/__init__.py`. There are no Pydantic re-exports, duplicate model
  verbs, registry, loader, code generator, global model settings, or global
  pickle reducers.

## Engineering rules

- Do not use `from __future__ import annotations`. Quote a forward reference only
  when its name is defined later. Eager annotations are required for reliable
  Pydantic schema transport of notebook-defined classes across Python 3.10-3.14.
- Preserve the lifecycle-enforcing Pydantic settings documented in `DESIGN.md`.
  Project-specific policy, such as strictness, belongs in a project base class.
- Prefer small explicit functions and ordinary Pydantic behavior over new
  framework layers, dynamic dispatch, registries, or compatibility shims for
  removed APIs.
- If behavior changes intentionally, update `DESIGN.md`, public tests, `README.md`,
  and `SKILL.md` together. Export a new public name explicitly from
  `src/nshconfig/__init__.py`.
- Test public behavior and failure boundaries. The golden probes in
  `tests/typing_probes/` are part of the typing contract; changed diagnostics are
  behavior changes, not flakes.
- Transport tests spawn subprocesses as cloudpickle canaries. They are required
  coverage, not optional slow tests.
