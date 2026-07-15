# nshconfig coding instructions

`nshconfig` is a small typed configuration lifecycle over Pydantic. Read
`DESIGN.md` before changing semantics; it is authoritative. `README.md` introduces
the library and `SKILL.md` gives the canonical usage workflow.

## Ownership and public surface

Pydantic owns schema declaration, aliases, constraints, validation,
serialization, and JSON Schema. Application code imports `Field`, validators,
`ConfigDict`, and other authoring APIs from `pydantic`.

Calling `ConfigType(...)` returns an ordinary validated final.
`ConfigType.config_draft()` creates mutable incomplete composition state, and
`draft.config_finalize()` validates it into a fresh final. Interpolation is a
whole-field callable evaluated in declaration order over canonical earlier values.

Normal constructor finals used as defaults are templates. A parent draft projects
default-origin Config values to fresh drafts through concrete structural
annotations. Explicitly assigned finals remain finals.

The public package surface is `Config`, `Context`, `interp`, `is_draft`,
`DraftError`, `UnsetError`, and `__version__`. Do not add Pydantic re-exports,
top-level draft/finalize aliases, provenance, records, fingerprints, dynamic verb
dispatch, registries, loaders, code generation, global model settings, or global
pickle reducers.

## Semantic invariants

- Draft creation runs no Pydantic hooks. Draft writes are unvalidated and unknown
  fields fail immediately.
- Finalization is explicit and non-destructive. Drafts cannot be copied or
  serialized.
- Pydantic field declaration order is interpolation dependency order. A derived
  field sees only earlier canonical values after complete field validation.
- Model hooks and final `model_copy()` retain native Pydantic semantics. Copy
  updates are unvalidated and model-after hooks may make interpolation stale.
- Structural Config positions need concrete annotations. Do not hide Config
  values under `Any`, `object`, or incompatible built-in positions. Arbitrary user
  objects are opaque.
- Finals are shallowly field-frozen. Drafts are unhashable; finals use
  frozen-Pydantic field-value hashing.
- Cloudpickle is optional, trusted, short-lived executable transport. It is not a
  durable or safe data format.

## Python and testing rules

- Target Python 3.10-3.14 and Pydantic `>=2.13,<3`.
- Never add `from __future__ import annotations`. Quote forward references only
  when a name is defined later; eager annotations keep notebook and cloudpickle
  schema transport reliable.
- Prefer small explicit functions and native Pydantic behavior over framework
  abstractions or compatibility layers.
- Test through the public API. Update `DESIGN.md`, public tests, `README.md`, and
  `SKILL.md` together for intentional semantic changes.
- Keep `uv run basedpyright src` at zero errors and warnings. Golden diagnostics
  in `tests/typing_probes/` are contractual. Subprocess transport tests are
  required cloudpickle canaries.

## Verification

```bash
uv sync --locked --all-groups --all-extras
uv run pytest
uv run ruff check src tests
uv run basedpyright src
uv run sphinx-build -W --keep-going -b html docs/source docs/build/html
uv run nox -s tests
uv build --clear
```
