# nshconfig coding instructions

`nshconfig` is a typed configuration lifecycle for ML runs, built directly on
Pydantic. Read `DESIGN.md` before changing semantics; it is authoritative.
`README.md` introduces the library and `SKILL.md` gives the canonical usage
workflow.

## What the library owns

Pydantic owns schema declaration, aliases, constraints, validation,
serialization, and JSON Schema. Application code imports `Field`, validators,
`ConfigDict`, and other authoring APIs from `pydantic`.

`nshconfig` owns four distinct states:

1. A `Config` subclass declares a schema.
2. `draft(ConfigType)` creates mutable, incomplete Python composition state.
3. `finalize(draft)` resolves interpolation and validates a field-frozen final.
4. `record(final)` creates inert JSON data for a concrete run.

The public surface is explicitly exported from `src/nshconfig/__init__.py`:
`Config`, `Context`, `draft`, `interp`, `finalize`, `is_draft`, `source`,
`explain`, `provenance`, `fingerprint`, `record`, `load_record`, `RunRecord`,
the event/explanation types, and lifecycle errors.

Do not add Pydantic re-exports, instance-method aliases for lifecycle functions,
dynamic verb dispatch, registries, loaders, code generation, process-global model
settings, or process-global pickle reducers.

## Semantic invariants

- A normal `Config` constructor returns a validated final. A draft is created only
  with `draft()` and accepts values through ordinary declared-field assignment.
- `finalize()` is explicit, non-destructive, and rejects cycles or pending values
  outside the supported structural graph.
- Pydantic field declaration order is interpolation dependency order. Derived
  fields see only earlier canonical values after their complete field validation.
- A structural `Config` position needs a concrete annotation. Do not hide drafts
  or interpolation markers under `Any`, `object`, or opaque objects.
- Provenance records draft assignment, deletion, tracked built-in container
  mutation, and interpolation. Provenance never changes final equality or
  fingerprints.
- Finals are field-frozen, not deeply immutable. Every `Config` is unhashable;
  deterministic identity comes from `fingerprint()`.
- Run records are JSON-safe dead data. `load_record()` validates concrete stored
  values and never executes interpolation.
- Cloudpickle is trusted, ephemeral executable transport for drafts and
  notebook-local classes. It is not a durable or safe record format.

## Python and testing rules

- Target Python 3.10-3.14 and Pydantic `>=2.13,<3`.
- Never add `from __future__ import annotations`. Quote forward references only
  when a name is defined later; eager annotations keep notebook/cloudpickle
  schema transport reliable.
- Prefer small explicit functions and native Pydantic features over framework
  abstractions or compatibility layers.
- Test through the public API. Update `DESIGN.md` and the user guides alongside an
  intentional semantic change.
- Keep `uv run basedpyright src` at zero errors and warnings. Golden diagnostics
  in `tests/typing_probes/` are contractual. Subprocess transport tests are
  required cloudpickle canaries.

## Verification

```bash
uv sync --locked --all-groups --all-extras
uv run pytest
uv run ruff check src tests
uv run ruff format --check src tests
uv run basedpyright src
uv run sphinx-build -W --keep-going -b html docs/source docs/build/html
uv run nox -s tests
uv build --clear
```
