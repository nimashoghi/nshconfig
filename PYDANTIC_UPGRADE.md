# What newer Pydantic buys nshconfig

Date: 2026-06-11. Companion to `REVIEW.md` (which argued for raising the Pydantic floor to *delete*
compat code; this document is about what we *gain*). Sources: GitHub release notes for every minor
from v2.8.0 through v2.13.4 plus the v2.14.0a1 pre-release (fetched via `gh release view`), the
Pydantic experimental-features docs, and hands-on verification of the 2.10-2.12 features against
the pydantic 2.12.5 installed in this repo's venv. Features marked "verified" were exercised in a
REPL; 2.13+ items are from release notes only (not installed here).

Current state for reference: nshconfig declares `pydantic>=2`, carries shims for 2.0-2.7, and its
nox matrix tests every 2.x release. Latest stable Pydantic is 2.13.4 (2026-05-06); 2.14.0a1
(2026-05-22) is out.

---

## 1. Release timeline, filtered to what matters for nshconfig

| Version | Date | Relevant items |
|---|---|---|
| 2.8 | 2024-07 | Experimental pipeline API; `fail_fast`; `defer_build` for TypeAdapter (experimental); `deprecated` in JSON schema; smart-union matching rework (behavior change); Python 3.13 support |
| 2.9 | 2024-09 | Import-time perf work begins; validators can customize their JSON schema; `json_schema_extra` dict merging (behavior change); `use_enum_values` respected on `Literal` |
| 2.10 | 2024-11 | **Data-aware `default_factory`** (#10678); **`experimental_allow_partial`**; `defer_build` stabilized everywhere + `TypeAdapter.rebuild()`; `__replace__` protocol (`copy.replace`, 3.13+); `propertyNames` in JSON schema; public `sort` method on schema generation; subclassable `ValidationError`/`PydanticCustomError`; Literal/Enum JSON schema changes |
| 2.11 | 2025-03 | **Schema-build perf/memory overhaul**; `__setattr__` handler caching (#10868, the mechanism behind REVIEW.md bug B1); experimental **free-threading** support; alias config APIs (`validate_by_name`/`validate_by_alias`/`serialize_by_alias`); `with_config(**kwargs)`; `create_model` rework; instance access to `model_fields` deprecated; drops Python 3.8 |
| 2.12 | 2025-10 | **Experimental `MISSING` sentinel** (#11883); **`__pydantic_on_complete__`** hook; **runtime `extra` override on validate calls** (#12233); `exclude_computed_fields`; field-level `exclude_if`; `ValidateAs` helper; `union_format` for JSON schema; PEP 728 (closed TypedDicts); initial Python 3.14 support; experimental features no longer emit runtime warnings |
| 2.13 | 2026-04 | **`polymorphic_serialization`** option; large validation/serialization perf batch (Literal validator optimization, JSON-by-iteration, regex caching); MISSING sentinel maturation (constraint push-down in unions, `exclude_unset` integration, nested-model serialization fix); deterministic JSON schema (sorted sets); pydantic-core repo merged into pydantic |
| 2.14a1 | 2026-05 | **Drops Python 3.9**; removes `eval_type_backport`; `model_copy()` only deep-copies non-updated fields |

---

## 2. The big opportunities, in priority order

### 2.1 Upstream `MISSING` sentinel: same name, different concept

Pydantic 2.12 shipped `pydantic.experimental.missing_sentinel.MISSING`, a PEP 661
`typing_extensions.Sentinel`. Verified on 2.12.5: `timeout: int | MISSING = MISSING` builds and
validates, `value is MISSING` discrimination works, and fields holding `MISSING` are **dropped from
`model_dump()`/`model_dump_json()` entirely** and **omitted from the JSON schema**. 2.13 invested
further (constraints in unions are pushed down past the sentinel; `exclude_unset` integration;
nested-model serialization fix), so it is headed for stabilization.

Despite the shared name, this is a different concept from nshconfig's `MISSING`, and the
difference drives every conclusion below:

- **nshconfig's `MISSING` is a transient placeholder for deferred initialization.** Its contract
  is "not set yet; will be resolved no later than validation/finalize", either by the user
  supplying a value or by resolution logic computing a dynamic default (the `__post_init__`
  fill-in idiom that nshconfig's own MISSING docs use as their canonical example). After that
  boundary the attribute is a plain `T`, which is exactly why the `AllowMissing[T] = T` typing
  trick is honest: post-resolution reads need no narrowing. It gives you a regular attribute with
  a dynamically resolved default.
- **Pydantic's `MISSING` is a persistent absence marker.** Its contract is "may stay unset
  forever; the absence is itself the information"; consumers narrow at every read site
  (`cfg.timeout if cfg.timeout is not MISSING else defaults["timeout"]`). The design choices
  follow from that contract: absence round-trips as omission (dropped from dumps), the sentinel
  is invisible in the JSON schema, and the `T | MISSING` union typing forces read-site narrowing,
  which is honest for *their* semantics.

The mechanical differences are consequences of the semantic one, not implementation taste:

| Aspect | nshconfig `MISSING` | Pydantic `MISSING` |
|---|---|---|
| Meaning | Unresolved; must be resolved by finalize | Absent; may remain absent forever |
| Resolution site | Once, at the validation/finalize boundary | Every read site, or never |
| Typing | `AllowMissing[T]` resolves to `T` (honest post-finalize) | `T \| MISSING` union (forces narrowing); pyright >= 1.1.402 with `enableExperimentalFeatures` |
| Serialization | Sentinel serialized explicitly (an unresolved draft on disk should say so) | Field omitted (absence is the data) |
| JSON schema | Sentinel visible | Sentinel invisible |
| Pickling | Works (BaseModel singleton via patched `__new__`) | **Not supported** per docs |
| Status | Stable, ours | Experimental |

Theorycrafting, with the semantics in view:

- **Not a replacement backend for `AllowMissing`, even once stable.** An earlier draft of this
  document framed upstream MISSING as a convergence path with a later backend swap; that framing
  was wrong. Swapping it in would import read-time-absence semantics into a
  deferred-initialization feature: an unresolved draft would serialize with its placeholders
  silently omitted (indistinguishable from "field never existed"), and the union typing would
  force narrowing at exactly the read sites `AllowMissing` exists to keep clean. The pickling and
  pyright blockers are real but secondary; the semantic mismatch is the disqualifier.
- **The dump question dissolves at the right layer.** Upstream's clean dumps are correct for
  their semantics, not a behavior to copy. For nshconfig, once `finalize()` enforces resolution
  (REVIEW.md B5), finalized configs cannot contain MISSING, so finalized dumps are clean by
  construction; and for *draft* dumps the explicit sentinel is the correct behavior, because a
  serialized work-in-progress should distinguish "still to be resolved" from "absent".
- **Where upstream MISSING does fit nshconfig: as a separate, future tri-state feature.** ML
  configs occasionally want genuine absence-is-data fields ("unset means let the framework
  decide, and the dump should not pin a value"), distinct from both `None` and a resolvable
  placeholder. If that demand materializes, expose pydantic's sentinel under a distinct name
  (`C.UNSET`, say) so the two concepts never blur. Blocked today by the pickling limitation
  (cloudpickle-based job submission) and the pyright experimental flag; revisit when stable.

### 2.2 Data-aware `default_factory`: within-model interpolation, natively, today

Since 2.10, `Field(default_factory=lambda data: ...)` receives the already-validated fields of the
same model. Verified on 2.12.5, including through `model_construct` (which drafts use):

```python
class DiffusionConfig(C.Config):
    dim: int = 256
    cond_dim: int = Field(default_factory=lambda data: 2 * data["dim"])
```

This is the missing local tier of the interpolation design in REVIEW.md section 5: sibling-field
derivation needs no `Ref`, no resolution pass, and is overridable (it is just a default). The
blessed pattern table becomes:

| Scope of the rule | Mechanism |
|---|---|
| Same model, depends on sibling fields | `default_factory=lambda data: ...` (2.10+) |
| Cross-module, root-relative | `C.ref(Root, lambda c: ...)` resolved at `finalize()` (proposed) |
| Invariant check, any scope | `model_validator(mode="after")` at the root |

Note that this is the declarative form of nshconfig's own canonical `MISSING` idiom: the MISSING
docs' `age_str` example (`AllowMissing[str] = MISSING` plus a `__post_init__` fill-in) is, for
same-model rules, exactly `Field(default_factory=lambda data: str(data["age"]))`. The
MISSING-plus-hook form stays relevant where the rule needs the draft flow or root-level context.

Two caveats to document if adopted:

- Factories see only fields defined *before* them (definition order matters), and only same-model
  data, with `data` typed as `dict[str, Any]` (no static checking of the lookup; a thin typed
  helper such as `C.sibling("dim", lambda d: 2 * d)` could partially recover that, though probably
  not worth it).
- Evaluation happens at construction, not at finalize. On a draft created empty and mutated
  (`d.dim = 1024` after `draft()`), the factory already ran against the original default, so the
  derived value is stale by finalize time. The `C.ref` mechanism deliberately resolves at
  `finalize()` for exactly this reason. Recommendation: in nshconfig docs, scope
  `default_factory(data)` to "configs constructed in one shot" and route draft-flow derivation
  through refs. Worth considering: have `model_construct_draft` *not* materialize data-aware
  factories (leave the field unset so finalize recomputes it); the
  `FieldInfo.default_factory_takes_validated_data` property (2.11) makes detecting them trivial.

This feature alone is a strong argument for a >= 2.10 floor.

### 2.3 Runtime `extra` override: config archaeology for ML runs

2.12 added an `extra` parameter to `model_validate` / `model_validate_json` / TypeAdapter
equivalents, overriding the class-level setting per call. Verified: an `extra="forbid"` model
loads data containing unknown keys with `model_validate(data, extra="ignore")`.

This solves a real, recurring ML-ops problem that `extra="forbid"` (correctly) creates: you rename
or remove a hyperparameter, and every config serialized into old run dirs, WandB, or checkpoints
now fails to load. Today the workaround is hand-editing JSON or subclassing with a different
config. With 2.12+, nshconfig can expose intent-revealing loaders:

```python
OldConfig = TrainConfig.from_json_file(path)                 # strict, default
OldConfig = TrainConfig.from_json_file(path, lenient=True)   # extra="ignore", logged warning
```

Theorycraft extensions: `lenient=True` could also collect-and-report the dropped keys (so the user
sees exactly what the old run had that the current schema lacks), which turns silent forward
compatibility into an auditable migration report. Pairs naturally with a `schema_version` field
convention for configs that need actual migrations.

### 2.4 Deferred builds, `__pydantic_on_complete__`, and the 2.11 perf overhaul: a cheaper Registry

The Registry's auto-rebuild is eager: every `register()` immediately calls
`model_rebuild(force=True)` on every dependent model, so importing a plugin package with N configs
referencing a registry with M registrations triggers N x M full schema builds at import time.
Three upstream developments change the calculus:

- 2.10 stabilized `defer_build` for models, dataclasses, and TypeAdapter, and added
  `TypeAdapter.rebuild()`.
- 2.11's schema-build overhaul (cached core schemas for parametrized generics, schema cleaning
  rewrite, annotation-application perf) makes each build much cheaper and less memory-hungry.
- 2.12's `__pydantic_on_complete__()` hook fires exactly when a model class becomes fully usable.

Redesign sketch: `rebuild_on_registers` stops rebuilding eagerly. Registration only marks
dependents dirty; dependents get rebuilt lazily on first validation (which `defer_build`
naturally provides, since the mock validator triggers a rebuild on first use), or explicitly via
`registry.flush()`. For the common import-then-train lifecycle, every model then builds exactly
once, after all plugins have registered, instead of M times. The dirty-marking can also fix a
latent ordering wart: today's registry discovery runs in `__pydantic_init_subclass__`, reading
`cls.model_fields` at a point where deferred or forward-referenced fields may be incomplete;
`__pydantic_on_complete__` is the lifecycle point that actually guarantees complete fields, so
discovery belongs there on 2.12+.

Same theme, smaller wins: nshconfig's `Config` subclasses are often defined at import time in big
trees (nshtrainer), so the 2.9/2.11 import-time and build-time improvements are free speedups for
exactly this library's usage profile; and `defer_build=True` could become a documented (maybe
default) option for large config packages.

### 2.5 Free-threading: the draft mechanism's class-state mutation becomes untenable

2.11 added experimental free-threaded CPython support and 2.13 requires its test suite to pass
free-threaded. Pydantic is positioning for a no-GIL world. nshconfig's current draft
implementation (temporarily mutating class-level `model_config`, plus the `_patched_post_init`
ClassVar toggle) is racy even with a GIL and simply broken without one. This is not a feature to
adopt so much as a deadline: the per-instance draft fix from REVIEW.md B1 is a prerequisite for
ever claiming 3.13t/3.14 support, and once it lands, adding a free-threaded job to CI (as Pydantic
itself does) would be cheap and differentiating for an ML-infra library, where dataloader worker
threads touching configs is a plausible reality.

### 2.6 Serialization contract cleanup: `exclude_if`, `exclude_computed_fields`, field names in errors

- `exclude_if` (2.12): per-field conditional exclusion, `Field(exclude_if=lambda v: ...)`. This
  offers a principled replacement for the `include_literals` hack (REVIEW.md B4). The current
  wrap-serializer exists to keep discriminator tags when users dump with `exclude_defaults=True`.
  Inverted with `exclude_if`: nshconfig could provide a "diff-style dump" (`to_dict(diff=True)`?)
  that attaches `exclude_if=lambda v, default=...: v == default` semantics to non-Literal fields
  rather than abusing `exclude_defaults` and then resurrecting tags after the fact. The
  `repr_diff_only` feature and this dump mode would then share one definition of "is default".
- `exclude_computed_fields` (2.12, verified): callers mixing computed convenience properties into
  configs can keep dumps as pure inputs, which matters for the "serialized config is exactly what
  reproduces the run" invariant.
- Field name in serialization errors (2.12) and the 2.13 perf work (Literal validator
  optimization, JSON validation by iteration) are free quality-of-life on upgrade.

### 2.7 `polymorphic_serialization` (2.13): the non-registry escape hatch

A long-standing Pydantic wart: a field annotated as `BaseConfig` holding a `SubConfig` serializes
only the base's fields unless you opt into `serialize_as_any` duck-typing (whose 2.12 behavior
caused enough issues that 2.13 introduced `polymorphic_serialization` to solve them properly).
For nshconfig this is interesting at the margins: the Registry with discriminated unions remains
the right answer for round-trippable plugin configs (it gives *validation* polymorphism too), but
for quick experiments where someone annotates `model: ModelConfigBase` and assigns an ad-hoc
subclass, 2.13 + `polymorphic_serialization` means the dumped artifact at least contains the
subclass's full data instead of silently truncating. Worth a docs section ("when you need the
Registry vs. when polymorphic serialization is enough") and possibly enabling it by default in
`Config.model_config` once the floor reaches 2.13. Verify interaction with `revalidate_instances`
before defaulting it (not testable on the installed 2.12.5).

### 2.8 Hyperparameter constraint vocabulary: pipeline API and `ValidateAs`

The experimental pipeline API (2.8) composes constraints fluently and, as of 2.12, no longer
emits runtime experimental warnings. Verified on 2.12.5:

```python
class TrunkConfig(C.Config):
    dim: Annotated[int, validate_as(int).ge(64).multiple_of(8)]
```

The transcript's "integer divisible by 8" example is literally a one-liner. Theorycraft: nshconfig
could ship a tiny ML-flavored annotation vocabulary (`C.DivisibleBy(8)`, `C.PowerOfTwo`,
`C.Probability`, `C.PositiveLR`) implemented with stable `annotated_types` where possible and the
pipeline API where composition is needed. Cheap to build, high leverage for the "config should
make invalid states unrepresentable" philosophy. The pipeline API itself should stay an internal
detail (it is explicitly proof-of-concept grade), but the vocabulary API we expose can outlive
whichever mechanism implements it.

### 2.9 Codegen modernization (if the export tooling stays)

Several upgrades line up specifically with the TypedDict/JSON-schema generator:

- PEP 728 closed TypedDicts (2.12): `extra="forbid"` configs should generate
  `class FooTypedDict(TypedDict, closed=True)` instead of the current `@typ.final` approximation;
  type checkers then reject unknown keys in literal dicts, which is the entire point of
  generating TypedDicts.
- `union_format` (2.12, verified) and the 2.10 Literal/Enum schema changes give cleaner unions in
  generated schemas; the `deprecated` field support (2.8) lets deprecation flow from `Field` to
  generated artifacts.
- The public `sort` method on JSON schema generation (2.10) plus deterministic set sorting (2.13)
  can replace the hand-rolled `_sort_json_schema` and make generated files genuinely
  reproducible across runs, which matters because they get committed.
- `propertyNames` support (2.10) fixes a real hole in the schema-to-TypedDict converter's
  dict handling (`json_schema.py` already reads `propertyNames` but upstream schemas only
  started emitting it in 2.10).

### 2.10 Sweep ergonomics: `__replace__` and cheaper copies

2.10 implements `__replace__`, so on Python 3.13+ `copy.replace(cfg, lr=3e-4)` works on configs;
2.14 will make `model_copy()` skip deep-copying updated fields. The transcript's sweep loop
("create base config, copy it, set the LR, submit") is exactly copy-heavy config manipulation.
nshconfig could lean in with a one-liner documented idiom, or a thin
`cfg.sweep(lr=[1e-4, 3e-4, 1e-3])` helper yielding validated copies; either way the upstream work
makes the underlying operation cheaper and more idiomatic.

### 2.11 Things to watch but not chase

- `experimental_allow_partial` (2.10): tempting as a draft-mode replacement, but verified and
  documented semantics say otherwise: TypeAdapter-only, supports only trailing-incomplete
  collections/TypedDicts (`NotRequired`/`total=False`), models validate all-or-nothing. Drafts
  need arbitrary holes, not stream prefixes. Re-evaluate if upstream ever generalizes it; the
  draft/finalize machinery remains nshconfig's job for now.
- `generate_arguments_schema()` (2.11): could type-check `__create_config__()` factories or a
  future CLI override layer, but nothing today needs it.
- Temporal validation/serialization config (2.12): only relevant if configs grow
  datetime-typed fields (run timestamps); fine to ignore.

---

## 3. Recommended floor and rollout

**Set `pydantic>=2.12,<3`.** Rationale: 2.12 is the knee of the curve. It is the first version
with the runtime `extra` override (2.3), `__pydantic_on_complete__` (2.4), warning-free
experimental features (2.8 section), and Python 3.14 support, while already including the 2.10
factories (2.2) and the 2.11 perf overhaul (2.4). A 2.10 floor would capture 2.2 only; the gap
between 2.10 and 2.12 costs almost nothing to skip (2.11's only API loss would be alias-config
niceties, which 2.12 includes anyway). Anyone able to install nshconfig 0.57+ in 2026 can install
pydantic 2.12.

Rollout sketch:

1. Floor bump + shim deletion (REVIEW.md section 4.5): remove the 2.4.2-2.5.3 monkey-patch,
   legacy hash path, `with_config` fallback, `defer_build` conditionals, `_PackageVersion`
   framework. Shrink the nox matrix from "every 2.x release" to {2.12.latest, 2.13.latest} per
   Python; add one free-threaded 3.13t/3.14 job once the B1 fix lands.
2. Land the B1 draft fix first (it is required regardless of floor; 2.11+ makes the current code
   actively wrong, and the installed 2.12.5 already exhibits it).
3. Adopt in order of leverage: lenient loading (2.3), registry lazy rebuild (2.4), document
   data-aware factories with the draft caveat (2.2), `exclude_if`-based dump cleanup replacing
   `include_literals` (2.6), constraint vocabulary (2.8).
4. Track, do not adopt: upstream MISSING stays off the `AllowMissing` path entirely (2.1, the
   semantics differ); it is only a candidate for a future, separately named tri-state feature
   once stable and picklable. `polymorphic_serialization` default-on when the floor reaches 2.13.
5. Plan the Python floor with 2.14: pydantic 2.14 drops Python 3.9 and `eval_type_backport`.
   nshconfig should drop 3.9 in the same release window (this also lets the codegen emit `X | Y`
   unions legally and deletes nshconfig's own `eval_type_backport` dependency marker).

---

## 4. Upgrade risks and behavior changes to absorb

Moving the floor across 2.8-2.13 means users skipping from old pydantic inherit these behavior
changes; nshconfig's release notes should name them:

- **Smart-union matching rework (2.8)**: untagged unions in config fields can pick a different
  member than before. Configs using the Registry (tagged unions) are immune, which is a good
  reason to push registry adoption in the same release.
- **`json_schema_extra` dict merging (2.9)** and **Literal/Enum JSON schema changes (2.10)**:
  regenerate any committed codegen output and JSON schema goldens; diffs are expected.
- **Instance access to `model_fields` deprecated (2.11)**: nshconfig's own code already uses
  `type(self).model_fields`, but user code reached via docs examples may not; worth a note.
- **After-model validators no longer implicitly converted to classmethods (2.12)**: user configs
  with bare-function `model_validator(mode="after")` callbacks may need a decorator fix.
- **`serialize_as_any` issues in 2.12**: if any downstream used it, route them to 2.13's
  `polymorphic_serialization` instead.
- **Experimental API churn**: pipeline, partial validation, and MISSING are explicitly unstable;
  anything nshconfig builds on them should sit behind nshconfig-owned names (`C.DivisibleBy`,
  `AllowMissing`) so upstream churn is absorbed in one module, not user code.
- The 2.13 packaging change (pydantic-core merged into the pydantic repo) is transparent for
  consumers but matters for anyone pinning pydantic-core directly (the noxfile does not, good).

---

## 5. One-paragraph summary

Raising the floor to pydantic 2.12 converts roughly six releases of upstream investment into
nshconfig features: native sibling-field derivation that slots under the planned `C.ref`
interpolation as its local tier, per-call lenient loading that solves old-run config loading
without weakening `extra="forbid"`, lifecycle hooks and deferred builds that turn the Registry's
eager rebuild storms into at-most-once lazy builds, a serialization toolkit (`exclude_if`,
`exclude_computed_fields`) that replaces the library's most contract-violating hack, and a
credible path to free-threaded Python. Meanwhile upstream's experimental MISSING sentinel shares
a name with nshconfig's but not a meaning: theirs marks persistent absence, resolved (if ever) at
each read site; ours is a deferred-initialization placeholder, a regular attribute with a
dynamically resolved default, gone by the end of `finalize()`. Keep the concepts separate, make
`finalize()` enforce ours (REVIEW.md B5), and consider exposing theirs later under a distinct
name for genuine tri-state fields.
