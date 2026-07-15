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

# Python 3.10-3.14 against the Pydantic floor and latest 2.x.
uv run nox -s tests

# Documentation and distributions.
uv run sphinx-build -W --keep-going -b html docs/source docs/build/html
uv build --clear
```

Use `./scripts/publish.sh` only for an intentional release.

## Architecture

- Pydantic owns schemas, aliases, validation, serialization, and JSON Schema.
  nshconfig re-exports its non-deprecated authoring API unchanged.
- `nshconfig` adds three instance states: an inert unbound template, a mutable
  incomplete draft, and a validated, shallowly field-frozen final.
- Calling a `Config` class first performs normal Pydantic validation. It creates
  an unbound template only when interpolation lacks enclosing context (or raises
  `NameError`) and every other error is a missing required field. Other Pydantic
  entry points never create templates.
- Create mutable composition state only with `ConfigType.config_draft()` and
  cross the validation boundary with the non-destructive
  `draft.config_finalize()` method.
- Normal constructor finals retain replayable default recipes. Parent-dependent
  `Child()` defaults become unbound templates and bind under their parent's
  normal Pydantic pipeline. Parent drafts project both forms to fresh drafts
  recursively through concrete structural annotations. Explicit finals remain
  finals. Factories returning drafts or templates are rejected.
- Interpolation is a whole-field Python callable. Field declaration order is
  dependency order, and interpolation may read only canonical values whose
  complete field validation has already finished.
- Drafts and templates are unhashable. Finals use frozen-Pydantic field-value
  hashing and are hashable exactly when their field values are hashable.
- `model_copy()` and model validators retain native Pydantic behavior on finals.
  Draft copies and template model copies are rejected. Templates support normal
  Python copy. Cloudpickle is optional, trusted, short-lived executable transport.
- Structural `Config` positions must have concrete annotations. Drafts and
  interpolation markers may not be hidden under `Any`, `object`, or incompatible
  built-in positions. Arbitrary user objects are opaque.
- The public API is the explicit surface in `src/nshconfig/__init__.py`. There are
  no top-level draft/finalize functions, provenance, records, fingerprints,
  registry, loader, code generator, global model settings, or global pickle
  reducers. Pydantic authoring names are explicit re-exports, not wrappers.

## Engineering rules

- Support eager annotations, quoted forward references, and
  `from __future__ import annotations`, including by-value cloudpickle transport
  of notebook-defined classes across Python 3.10-3.14.
- Preserve the lifecycle-enforcing Pydantic settings documented in `DESIGN.md`.
  Strict validation and attribute-docstring descriptions are default policies;
  project-specific policy belongs in a project base class.
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
