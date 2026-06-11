# Migrating from v1

v2 is a greenfield rewrite; there is no compatibility layer. v1 remains available on PyPI
(`nshconfig<2`) and in git history. The table maps every v1 idiom to its v2 answer.

| v1 | v2 |
|---|---|
| `AllowMissing[T] = MISSING` + `__post_init__` fill-in (dynamically resolved default) | `field: T = C.interp(lambda c: ...)`; the `if x is MISSING` guard, the sentinel, and the hook all dissolve |
| `AllowMissing[T] = MISSING` as "user must provide before finalize" | a required field, simply unset on the draft; `finalize` fails per-leaf with provenance |
| `validate_no_missing()` | nothing: there is no sentinel to sweep for |
| `cfg = Cls.draft(); ...; cfg.finalize()` | `cfg = Cls.draft(); ...; C.finalize(cfg)` — but children auto-create now (no more pre-assigning child drafts), and finals are frozen |
| `Registry` / dynamic discriminated unions | plain pydantic: `Annotated[A \| B, Field(discriminator="kind")]`; for dump-side polymorphism on base-annotated fields see pydantic 2.13's `polymorphic_serialization` |
| `nshconfig-export` codegen, TypedDicts, JSON schemas | removed; `Cls.model_json_schema()` covers editor schemas |
| `to_yaml_*` / `to_toml_*` / `from_python_file` | removed; `model_dump_json`/`model_validate_json` are the record, configs are Python modules you import |
| `Config` as `MutableMapping` (Lightning hparams) | removed; pass `final.model_dump()` at the call site |
| `Singleton` / `singleton` | removed; a module-level variable is the singleton |
| `deduplicate(configs)` | finals are hashable: `list(dict.fromkeys(configs))` |
| `with_config`, pydantic wildcard re-exports | import from `pydantic` directly; nshconfig exports only its own names |

## What you gain

- Interpolation that v1 never had: Hydra-style upward and root references, at class definition
  *and* at composition time, mostly type-checked, resolved exactly once.
- Provenance: `C.explain(final, "optim.lr")` for "why did this run use that value".
- Frozen, hashable finals; `exclude_unset` dumps that mean "what the user chose"; `thaw` for
  tweak-and-rerun; verified notebook-to-cluster transport of pending drafts.
- A draft mechanism that is structurally immune to the v1 setattr-cache unsoundness, safe
  under threads, and statically checked (typo'd draft writes are basedpyright errors).

## Floors

Python >= 3.10 (was 3.9), pydantic >= 2.13 (was >= 2.0). The version-shim layer is gone.
