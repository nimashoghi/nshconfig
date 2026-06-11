# nshconfig: architecture review

Date: 2026-06-11. Reviewed at commit 41d3317 (v0.56.0), pydantic 2.12.5, Python 3.12.
All bugs below were reproduced live in a REPL against the installed package; repro snippets are included.

Scope and framing: the stated goal is a clean, Pydantic-backed config architecture for storing
training hyperparameters in ML runs, with a Pythonic user-facing API as the primary product. The
meeting transcript adds a second goal: grow an OmegaConf-style interpolation capability that is
reliable, fits the draft/finalize philosophy, and is type-safe where feasible. This review covers
(1) what the library gets right, (2) confirmed bugs, (3) an architecture critique through the
"abstractions must pay rent" lens, (4) API recommendations, and (5) a concrete interpolation design.

---

## 1. Summary

The core idea is sound and differentiated: Pydantic models with a two-phase lifecycle
(`draft()` -> mutate -> `finalize()`), a typed `MISSING` sentinel, and a registry for dynamic
discriminated unions. That is exactly the right skeleton for the "config helpers are plain Python
functions that mutate a draft" workflow, and it is the right foundation to build interpolation on.

But the implementation of the draft mechanism is built on temporarily mutating *class-level*
Pydantic state, and on current Pydantic that is not just thread-unsafe, it is broken: one draft
assignment permanently disables `validate_assignment` for that field across the whole class
(bug B1, the most important finding here). Several secondary features (the `MutableMapping`
facade, the `include_literals` serializer) silently violate their own contracts. And more than
half of the package by line count is codegen tooling at 13-16% test coverage, which dilutes the
core product.

The good news: every serious problem is fixable without changing the user-facing API, and the
library's existing `MISSING` design points directly at a clean, mostly type-safe interpolation
design (section 5).

---

## 2. What the library gets right

- **The draft/finalize lifecycle** (`src/nshconfig/_src/config.py:261-275`) is a genuinely better
  authoring UX than both giant-constructor-call and YAML. It preserves "the config is just data"
  (no Hydra `_target_`), and it gives validation a single, well-defined choke point. The
  `__draft_pre_init__` / `__post_init__` hooks are the right extension seams.
- **`AllowMissing[T]` / `MISSING`** (`missing.py`) is clever and well-executed: typed as the field
  type for the type checker, a real singleton at runtime, JSON-round-trippable (verified), and
  identity-stable through `deepcopy` (verified). Its semantics are worth stating precisely,
  because they are unusual and they shape everything below: `MISSING` is a *transient
  placeholder* meaning "not set yet, to be resolved no later than validation/finalize", either by
  the user supplying a value or by resolution logic (`__post_init__`/`__draft_pre_init__`)
  computing a dynamic default. It is not a persistent "absent" marker. That contract is exactly
  why the `AllowMissing[T] = T` typing trick is honest rather than a lie: after the lifecycle
  boundary the attribute really is a plain `T` (the docs' `age_str` example reads it without any
  narrowing). This deferred-initialization pattern, a regular attribute with a dynamically
  resolved default, is the pattern interpolation should copy.
- **Validation posture**: `validate_assignment`, `validate_default`, `extra="forbid"`,
  `revalidate_instances="always"` (`config.py:173-197`) match the "hard error on states that
  should not happen" philosophy from the transcript.
- **Registry** (`registry.py`) solves a real Pydantic pain (discriminated unions whose membership
  grows after the parent schema is built) with auto-rebuild via `__pydantic_init_subclass__`
  scanning. The docstrings are excellent. This is an abstraction that pays rent for plugin-style
  model zoos.
- **Notebook ergonomics**: treescope integration with default-value dimming
  (`config.py:437-466`, `treescope_util.py`) directly serves the notebook-first workflow.
- **Editor-facing serialization**: `$schema` injection into JSON and the
  `# yaml-language-server: $schema=...` header (`config.py:560-575, 665-668`) is a thoughtful
  bridge for collaborators who still read/write YAML.
- **Typed class-keyword config**: the `__init_subclass__` overload with `Unpack[ConfigDict]`
  (`config.py:199-203`) makes `class Foo(Config, repr_diff_only=True)` type-check.
- Project health basics are in place: 104 tests pass, basedpyright standard mode is clean, ruff is
  clean, and core modules (config 75%, missing 97%, singleton 97%) have decent coverage.

---

## 3. Confirmed bugs

### B1 (critical): one draft assignment permanently disables assignment validation for the class

`Config.__setattr__` wraps draft assignments in `_nshconfig_patch_validator_validate_assignment`
(`config.py:319-346`), which temporarily flips `self.model_config["validate_assignment"]` to
`False`. `model_config` is the *class* dict, so this was always a thread hazard. On current
Pydantic it is worse: Pydantic caches a per-field setattr handler in
`__pydantic_setattr_handlers__` the first time a field is assigned. If that first assignment
happens on a draft, the cached handler is the *non-validating* one, and it is reused for every
later assignment on every instance:

```python
class Y(C.Config):
    v: int = 1

Y().v = "garbage"        # ValidationError, as expected

yd = Y.draft(); yd.v = 5 # one draft assignment...
Y().v = "garbage"        # ...now silently ACCEPTED on a fresh, normal instance
```

Verified on pydantic 2.12.5 (`Y.__pydantic_setattr_handlers__` afterwards holds the
non-validating `_model_field_setattr_handler` for `v`). Any Pydantic version with this cache is
affected; older versions "only" have the cross-instance/cross-thread race.

**Fix**: never touch class state. For draft instances, bypass Pydantic's setattr machinery with a
direct dict write, which is per-instance, thread-safe, and never populates the handler cache:

```python
@override
def __setattr__(self, name: str, value: Any) -> None:
    if (
        self._is_draft_config
        and name in type(self).model_fields
        and self.model_config.get("no_validate_assignment_for_draft", True)
    ):
        self.__dict__[name] = value
        self.__pydantic_fields_set__.add(name)
        return
    super().__setattr__(name, value)
```

The same class-level-mutation pattern appears in `_patched_post_init` (`config.py:277-298`),
used to suppress `__post_init__` during `model_construct_draft`. It does not poison any Pydantic
cache, but it is racy under concurrent construction (a parallel normal construction of the same
class while any draft is being created skips its `__post_init__`). Pass the flag through the
construction path (e.g. include it in `values` and check `_is_draft_config` inside
`model_post_init`) instead of toggling a ClassVar.

### B2 (high): the `MutableMapping` write path is a silent no-op

`Config` inherits `MutableMapping[str, Any]` for Lightning hparams compatibility
(`config.py:371-435`). `__setitem__` writes into `self._nshconfig_dict`, which is a *property
that returns a fresh `model_dump()`* (`config.py:376-378`). Every write lands in a throwaway
dict:

```python
o = Outer()              # name="x", inner.dim=64
o["name"] = "changed"    # no error, no effect
o["inner.dim"] = 128     # no error, no effect
o.name, o.inner.dim      # ("x", 64)
```

This is the worst failure mode for a config library: a user believes they overrode a
hyperparameter and the run proceeds with the old value. Recommendation in section 4.3: drop the
mutable facade entirely (read-only `Mapping` is enough for Lightning) and provide an explicit,
validated override API instead.

### B3 (high): `__init__` and `finalize()` now disagree about strictness

Commit d4cadf9 removed `"strict": True` from `model_config`, but `finalize()` and
`model_deep_validate()` still default to `strict=True` (`config.py:248-275`). The two
construction paths now accept different inputs:

```python
TC(steps="200")                 # OK: lax coercion
d = TC.draft(); d.steps = "200"
d.finalize()                    # ValidationError: int_type
```

Same data, different outcome depending on which idiom the user picked. Decide on one semantics
(my recommendation: lax in both, i.e. `finalize(strict: bool = False)` or
`strict: bool | None = None` meaning "follow the class config") and document it. Note also that
the bundled skill still advertises the old behavior ("stricter defaults: ... `strict=True`",
`src/nshconfig/_skill/SKILL.md:23`), so the LLM-facing docs are actively wrong after d4cadf9.

### B4 (medium): the `include_literals` serializer breaks `include`/`exclude`

The wrap serializer (`config.py:467-478`) unconditionally re-adds every `Literal`-annotated field
after the inner serializer runs:

```python
l.model_dump(include={"lr"})    # {'lr': 0.001, 'kind': 'adam'}   <- kind resurrected
l.model_dump(exclude={"kind"})  # {'lr': 0.001, 'kind': 'adam'}   <- exclusion ignored
```

It also writes under the field name (not the alias) when `by_alias=True`, and writes the live
Python object even in JSON mode (fine for str/int literals, wrong for enum literals). The intent
(keep discriminator tags when `exclude_defaults=True`) is good; the implementation should only
re-add fields that were dropped *by the defaults filter*, e.g. only when `exclude_defaults` is in
effect and the field is not explicitly excluded. The serialization context carries this
information (`info.exclude_defaults`, `info.include`, `info.exclude` via
`model_serializer(mode="wrap", when_used=...)` plus `SerializationInfo`).

### B5 (medium): MISSING enforcement is shallow and not wired into `finalize()`

Two related gaps (`missing.py:83-109`, `config.py:266-275`):

1. `validate_no_missing` only inspects the top-level model's fields. Nested configs (or configs
   inside lists/dicts) with `MISSING` values pass silently. Verified.
2. `finalize()` never calls it, so a "finalized" config can still contain `MISSING` holes,
   including ones the user never thought about because they live two levels down. Verified.

Under the library's own semantics, MISSING is a placeholder that must be resolved by validation
time (section 2); it is not a persistent absence marker. A finalized config still containing
MISSING is therefore a contract violation, not a hygiene issue, and the check belongs in
`finalize()` by definition: `finalize()` should perform a *recursive* no-missing check by
default, with an opt-out (`finalize(allow_missing=True)`) for the rare case that genuinely wants
to defer further. The recursive walk is ~20 lines
(descend into `Config` values, lists, dicts, tuples). This also becomes the natural place to
check for unresolved interpolation refs (section 5).

### B6 (low): the vendored Pydantic monkey-patch has a broken condition

`registry.py:133`: `if "schema_ref" and "ref" in definition:` evaluates as
`"ref" in definition` (the first operand is a constant truthy string). The upstream PR this was
copied from checks `'schema_ref' in definition and 'ref' in definition`. Consequence: for
pydantic 2.4.2-2.5.3, every ref-carrying definition takes the "purposely indirect reference"
branch and walk results are discarded. Nobody on a modern Pydantic hits this code; see section
4.5 for the better fix (delete the shim along with the legacy support).

### B7 (low): assorted

- **Dead code**: module-level `_model_config` dict (`config.py:128-143`) is never referenced (the
  class defines its own copy); the `is_exporting` global (`export.py:30,93-94,280`) is set and
  never read anywhere.
- **Log-level abuse**: routine messages logged at CRITICAL: `deduplicate` (`utils.py:221`) and
  the export tool's directory removal (`export.py:193`). These will page people whose handlers
  treat CRITICAL seriously. Use INFO/DEBUG.
- **Module-ignore inconsistency**: `_find_modules` filters the root module with `_is_submodule`
  but submodules with `full_module_name.startswith(ignore)` (`export.py:619`), so ignoring
  `mypkg.foo` also ignores `mypkg.foobar`. `_is_submodule` exists precisely to avoid this.
- **`_run_ruff` swallows everything** (`export.py:283-310`): if ruff is missing or fails, codegen
  output silently ships unformatted/broken imports. At least log a warning.
- **`import_python_file`** (`utils.py:53-84`): module name is `hash(str(path))` (salted per
  process, so unstable across runs), the module is never inserted into `sys.modules`, and there
  is no caching, so loading the same config file twice yields *distinct class objects*
  (verified: `mod1.Local is not mod2.Local`). Consequences: `isinstance` checks across loads
  fail, and classes defined inside a config file cannot be pickled normally. This matters for
  your cloudpickle-based runner workflow; at minimum register the module in `sys.modules` keyed
  by a stable digest of the resolved path, and cache repeat loads.
- **Draft kwargs are deep-copied** (`config.py:314`): `MyConfig.draft(model=<large object>)`
  deep-copies the value. For arbitrary types (`arbitrary_types_allowed=True` invites tensors,
  datasets, locks) this is expensive or raises. Either drop the copy and document that draft
  takes ownership, or copy only plain containers.
- **Default hash vs unhashable fields**: `set_default_hash` (`config.py:950-969`) installs a
  hash over `__dict__` values; any config with a `list` field raises `TypeError` when used as a
  dict key, so the feature works only for accidentally-hashable configs. Document, or hash the
  canonical JSON dump instead (stable, always works, slower).
- **Finalize noise**: a failing `finalize()` first emits Pydantic serializer warnings from
  dumping the half-built draft (observed: `PydanticSerializationUnexpectedValue`) before the real
  error. Dump with `warnings=False` inside `model_deep_validate`.
- **No `py.typed` marker** in `src/nshconfig/`. Per PEP 561, downstream type checkers (mypy
  certainly; pyright in strict package modes) treat the package as untyped. For a library whose
  tagline is "fully typed configuration management", this one-empty-file fix is the highest
  value-per-effort item in this document.
- **`registry._rebuild`** uses `model_rebuild(force=True, raise_errors=False)`
  (`registry.py:466`) and ignores the result; a rebuild failure leaves a stale schema with no
  signal. Log a warning when it returns False.

---

## 4. Architecture critique

### 4.1 The package is two products taped together

Rough volume by role:

| Concern | Modules | Lines | Coverage |
|---|---|---|---|
| Core config runtime | config, missing, singleton, adapter, registry, invalid, utils | ~2700 | 66-100% |
| Codegen/export tooling | export, json_schema, typing_inspect_ (vendored) | ~2400 | 13-16% |

The codegen half (TypedDict generation via a JSON-schema-to-Python-code pipeline, export-tree
generation, the `.nshconfig.generated.json` metadata protocol) is a developer tool that runs at
build time, yet it lives in the runtime package, contributes the `Export` symbol to the public
API, and is the least-tested code in the repo. The core runtime even imports from it at runtime
(`_get_schema_uri` -> `find_config_metadata`, `config.py:895-925`).

Recommendation: quarantine it. Move it under `nshconfig.codegen` (or a separate distribution),
keep only `find_config_metadata` reachable from the core, and stop letting its maintenance cost
compete with the core. Given the hparams goal, the core deserves nearly all the attention; the
TypedDict generator is the kind of abstraction that has to keep re-justifying its rent every time
Pydantic's JSON schema output shifts.

### 4.2 The `MutableMapping` base class is the wrong tool for Lightning compat

Beyond bug B2, inheriting `MutableMapping` makes every config `isinstance(x, Mapping)`. That has
ambient consequences: Lightning's `apply_to_collection` will descend into configs as if they were
containers, generic code that branches on Mapping treats configs as dicts, and Pydantic itself
will happily validate a config instance against a `dict[str, Any]` field. The dotted-path
`__getitem__` also re-runs a full `model_dump()` of the entire tree per key access.

Lightning only needs to *read* hparams. Recommendation (breaking, but per your stance on
backwards compatibility, worth it):

- Drop `MutableMapping`; if transparent Lightning support must stay, implement read-only
  `Mapping` only, and make any write raise immediately instead of vanishing.
- Add the explicit override API that `__setitem__` was pretending to be:

  ```python
  def with_overrides(self, overrides: Mapping[str, Any]) -> Self:
      """Return a new validated config with dotted-path overrides applied."""
  ```

  This also gives you the Hydra-CLI-style ergonomics Jordan asked for ("change a couple of
  things and run"): a five-line argparse loop mapping `--set trunk.dim=256` onto
  `with_overrides` gets the terminal workflow without a config DSL, and it is validated.

### 4.3 Lifecycle coherence: draft -> finalize should mean something stronger

Right now "finalized" guarantees only "the data validated once". It does not guarantee
completeness (B5), and the object remains mutable, with a subtle semantics flip: on a draft,
assigning a sub-config stores the object itself; on a finalized config,
`revalidate_instances="always"` makes assignment store a *copy* (verified: `p.sub = s;
p.sub is not s`), so mutations through the old reference silently stop propagating. None of this
is documented.

Recommendation: make `finalize()` the boundary it implies:

1. resolve interpolation refs (section 5),
2. recursive no-MISSING check by default,
3. validate,
4. optionally freeze (`model_config["freeze_on_finalize"]` or `finalize(freeze=True)`): with the
   `__setattr__` override from B1's fix, instance-level freezing is a two-line check on a private
   flag. Frozen finalized configs make the default hash actually safe to use, make accidental
   post-launch mutation impossible, and pair well with run reproducibility.

Also worth fixing while in here: drafts are not recursive. `Boltz.draft()` leaves a *required*
sub-config absent (`d.diffusion.dim = ...` raises `AttributeError`) and leaves a *defaulted*
sub-config as a normal validating instance, so attribute assignment behaves differently one level
down (verified both). The `boltz_large(cfg)` helper pattern from the transcript wants
`cfg.diffusion.dim = 128` to just work. Two options: auto-create drafts for required
`Config`-typed fields in `model_construct_draft`, or at least convert defaulted sub-configs to
drafts. The first is a significant UX win for exactly the workflow you describe and costs ~15
lines (instantiate `field.annotation.draft()` for fields whose annotation is a Config subclass).

### 4.4 Public API surface and packaging

- `from pydantic import *` in `pydantic_exports.py:6` makes nshconfig's public namespace track
  whatever pydantic's `__all__` is on the installed version, including deprecations. Pin it with
  an explicit re-export list; the surface should be yours, not upstream's.
- Naming asymmetries: `from_yaml` (file) vs `from_json_file`/`from_toml_file`; `Adapter` supports
  JSON/Python but not YAML/TOML; `to_toml_file(self, /, path)` places `path` after the
  positional-only marker while `to_json_file(self, path, /)` puts it before. Small, but this is a
  "Pythonic API is the product" library; rename `from_yaml` to `from_yaml_file` (keep alias) and
  mirror the formats across `Config` and `Adapter`.
- `draft(**kwargs)` is untyped (`config.py:261-264`), so field names are unchecked at draft
  creation while the same names are checked in `__init__`. If you keep the codegen tooling, this
  is what its TypedDicts are actually good for (`Unpack[MyConfigTypedDict]` overloads); if not,
  document that drafts trade static checking for flexibility.
- Add `py.typed` (see B7).
- The deprecated `DynamicResolution` carries a 95-line docstring (`registry.py:592-677`); cut it
  to two lines pointing at the replacement.

### 4.5 Version-compat surface: raise the floor

The repo supports Python 3.9+ and pydantic >=2.0, and the nox matrix tests every pydantic 2.x
release on four Pythons (`noxfile.py:63-64`), which is why there are 800 KB nox logs sitting in
the repo root. The cost shows up everywhere: the monkey-patch with the broken condition
(B6), `_legacy_pydantic_class_kwargs` (`registry.py:113-120`), the legacy hash path
(`config.py:928-947`), the `with_config` fallback shim (`config.py:24-36`), `defer_build`
branches, the `_PackageVersion` comparison framework (~100 lines in `utils.py:238-341` that
re-imports pydantic on every comparison), and the vendored 912-line `typing_inspect_`.

In 2026, nothing in your ML stack runs pydantic 2.0-2.6. Set `pydantic>=2.8` (or wherever
nshtrainer's floor is), delete the shims and the monkey-patch, replace `_PackageVersion` with a
cached three-liner over `importlib.metadata.version`, and shrink the nox matrix to
{oldest supported, latest} per Python. Separately decide the Python floor: 3.10 would drop
`eval_type_backport` and let the codegen emit `X | Y` unions legally (today
`_create_instance_or_dict_code` at `export.py:851-857` generates 3.10-only syntax into projects
while the package itself claims 3.9 support).

### 4.6 Docs and skill drift

- `SKILL.md` is stale on strict mode (B3) and ships inside the wheel, so agents are being taught
  wrong behavior. Treat the skill as part of the release checklist; better, generate the
  defaults table in it from `Config.model_config` in CI.
- The registry examples (docs and skill) put `def build(self)` methods on configs. That is a
  reasonable pattern, but it contradicts the stated philosophy that configs hold data and do not
  construct objects; the docs should either embrace "configs may carry builders" or show the
  builder as a free function dispatching on the config type. Pick one story.
- README promises "Built-in PyTorch Lightning integration"; the integration is the Mapping facade
  (see 4.2). After the facade changes, update the claim to describe the explicit bridge.
- `deduplicate` (`utils.py:200-223`) is O(n^2) and advertised as a feature; with frozen+hashable
  finalized configs (4.3) it becomes `dict.fromkeys` and the special casing disappears.

---

## 5. Interpolation: a design that fits this library

### 5.1 Requirements (from the transcript, made precise)

1. A field's value can be *derived from another part of the config tree* ("trunk.dim and
   diffusion.dim must match"), with the rule stated once, not enforced by remembering to edit two
   places.
2. Derived values must be overridable: interpolation supplies a default, an explicit assignment
   wins. (This distinguishes it from `computed_field`, which is read-only and not an input.)
3. Resolution happens at a well-defined time with loud failure: unresolved or cyclic refs must
   fail `finalize()`, never produce a half-resolved config.
4. Python-first and type-safe to the extent possible: no string mini-language as the primary
   interface; the rule should be checkable by pyright.
5. The finalized, serialized artifact contains concrete values only (what lands in the run dir
   and WandB is fully resolved, like a composed Hydra config).

Cross-field *constraints* ("fail if these disagree") are already served well by
`model_validator` at the root; interpolation is for *derivation*. The two compose: derive by
default, validate invariants regardless.

### 5.2 Options considered

- **OmegaConf-style strings** (`dim: int = "${trunk.dim}"`): zero type safety, needs a parser,
  escaping rules, and resolver registry; the exact "language inside a language" the transcript
  rejects. At most, accept this *syntax* later as a YAML-loading convenience that parses into the
  typed mechanism below. Not the core.
- **`computed_field`**: wrong semantics (not overridable, not an input field, excluded from
  validation-as-input).
- **Hooks filling MISSING** (the current idiom, and the MISSING docs' canonical example): works,
  and it is the intended meaning of MISSING (a dynamically resolved default), but the rule lives
  far from the field, grows into the "massive if-else" problem, and is invisible to helpers
  composing drafts.
- **Deferred reference objects resolved at finalize**: recommended; detailed below. It is the
  `MISSING` trick applied to derivation, so it inherits the library's existing mental model.

### 5.3 Recommended design: `C.ref` resolved in `finalize()`

A `Ref` is a small frozen object holding a function from the *root* config to a value. The
public constructor lies to the type checker the same way `MISSING` does, returning `T` so it can
sit anywhere a `T` is expected, while pyright fully checks the rule itself:

```python
class Ref(Generic[R, T]):
    __slots__ = ("fn", "label")
    def __init__(self, fn: Callable[[R], T], label: str | None): ...

def ref(root_cls: type[R], fn: Callable[[R], T], /, *, label: str | None = None) -> T:
    """A deferred reference, resolved against the root config at finalize time."""
    return cast(T, Ref(fn, label))
```

Usage, in exactly the config-helper style you already use:

```python
def boltz_large(cfg: BoltzConfig) -> None:
    cfg.trunk.dim = 1024
    cfg.diffusion.dim = C.ref(BoltzConfig, lambda c: c.trunk.dim)
    cfg.diffusion.cond_dim = C.ref(BoltzConfig, lambda c: 2 * c.trunk.dim)

cfg = BoltzConfig.draft()
boltz_large(cfg)
cfg.diffusion.dim = 768   # explicit override still wins: it replaces the Ref
cfg = cfg.finalize()      # refs resolved here, then validated; cfg.diffusion.cond_dim == 2048
```

Type-safety analysis:

- `lambda c: c.trunk.dim` is fully checked: `c` is inferred as `BoltzConfig` from the first
  argument, so a typo (`c.trnk.dim`) or a type mismatch (`str` field referenced into an `int`
  field) is a pyright error at the call site, because `ref`'s return type `T` must match the
  assigned field's type. This is the part Hydra/OmegaConf fundamentally cannot give you.
- The residual unsoundness is identical to `MISSING`: between assignment and `finalize()`, the
  field holds a `Ref` at runtime while the type checker believes it holds a `T`. That is the
  agreed price of the draft phase, and it is contained by the same mechanism (resolution +
  validation at the finalize boundary).
- For class-body defaults where the root class is not yet defined
  (`DiffusionConfig` is defined before `BoltzConfig`), offer an untyped-root form
  `C.ref(lambda c: c.trunk.dim)` (param typed `Any`). Helpers should prefer the typed form.

Resolution semantics (slots into the existing `finalize()` pipeline, step order from 4.3):

1. After `__draft_pre_init__`, walk the draft tree (configs via `__dict__`, plus lists, dicts,
   tuples). Collect fields whose current value is a `Ref`.
2. Resolve lazily with memoization: evaluating `fn(root)` may read a field that is itself an
   unresolved `Ref`; resolve it recursively first. Keep an in-progress set keyed by object
   identity; revisiting an in-progress ref is a cycle, reported with the full chain
   (`diffusion.dim -> trunk.dim -> diffusion.dim`).
3. If `fn` raises (`AttributeError` on an absent draft field, source value is `MISSING`), raise
   a `RefResolutionError` naming the target path and the `label` (or the lambda's source via
   `inspect.getsource` on a best-effort basis).
4. Write resolved values with the draft's direct-write setattr, then proceed to the existing
   dump-and-validate. Validation then type-checks every resolved value in context, which is what
   makes the runtime story sound even where the static story is not.

Properties this buys:

- **Reliability**: no resolution-at-read-time spookiness (OmegaConf's main operational hazard);
  refs cannot survive `finalize()`, because anything left unresolved either errors in step 2-3 or
  fails Pydantic validation ("expected int, got Ref") in step 4. Direct construction
  (`BoltzConfig(...)` without draft) with a Ref default also fails loudly rather than silently,
  and a Config-level before-validator can turn that into a "refs require the draft flow or
  AllowRef" message.
- **Serialization**: finalized dumps contain only concrete values (requirement 5). If you later
  want re-runnable "config programs", that is what `from_python_file` already is; and if YAML
  users want `${a.b}`, a loader hook can parse it into `C.ref` without touching the core.
- **Philosophy fit**: today `MISSING` carries two idioms: "the user must supply this before
  finalize" (deferred required), and "fill-in logic in `__post_init__` computes this at
  validation time" (the dynamically resolved default, which is the canonical example in the
  MISSING docs). `ref` is the declarative successor to the second idiom: the rule moves from a
  far-away hook into the assignment site that owns it, while `MISSING` keeps the first job. Same
  lifecycle, same enforcement point, same typing trick. The transcript's `INTERPOLATE` sentinel
  idea is subsumed: a bare sentinel cannot carry the rule, whereas `ref` carries it.

Scope estimate: ~200 lines plus tests (Ref type, tree walk, memoized resolver, error rendering),
no new dependencies, no changes to existing public API. Implement after B1/B5, since it reuses
the fixed draft setattr and the recursive walk from the deep no-missing check.

An optional second phase, only if class-level defaults become common: an `AllowRef[T]` annotation
mirroring `AllowMissing[T]` (union with `Ref` in the core schema) so that refs validate cleanly as
defaults in the non-draft path, with the root's `__post_init__` running the same resolution pass.
Defer until a real use case demands it; the draft-flow version covers the workflows in the
transcript.

### 5.4 What not to build

- No global resolver registry, no `${env:...}`/`${oc.select:...}` equivalents. Environment and
  machine-specific values should enter through the config helper or `with_overrides`, in Python,
  where they are visible and typed.
- No resolution on attribute access. Lazy interpolation is what makes OmegaConf configs
  irreproducible snapshots; resolve once, at the boundary, then the object is plain data.
- No string-path API as the primary interface (`config.interpolate("trunk.dim")` from the
  transcript brainstorm): it re-introduces stringly-typed coupling that pyright cannot see. Keep
  strings only at the serialization edge, if ever.

---

## 6. Prioritized actions

1. **Fix B1** (draft setattr cache poisoning) and the `_patched_post_init` race; this is the
   load-bearing bug. Add a regression test: normal-instance assignment after a draft assignment
   must still validate.
2. **Fix B2/4.2**: remove the mutable Mapping write path (read-only Mapping at most), add
   `with_overrides`. Add a test that writes through the facade and asserts it errors.
3. **Resolve B3**: pick one strictness, align `finalize()` and docs/SKILL.md with d4cadf9.
4. **B5 + lifecycle**: recursive `validate_no_missing`, called by `finalize()` by default;
   optional freeze-on-finalize; recursive drafts for sub-configs.
5. **Ship `py.typed`** and pin the public API (replace the pydantic wildcard re-export).
6. **Raise floors**: pydantic >=2.8-ish, trim shims/monkey-patch/`_PackageVersion`, shrink the
   nox matrix; decide on Python 3.10.
7. **Implement `C.ref` interpolation** per section 5.3.
8. **Quarantine codegen** under `nshconfig.codegen` (or separate package); fix its small bugs
   (startswith ignore, `_run_ruff` swallow, log levels) or consciously freeze it.
9. Sweep the B7 list (dead code, log levels, `import_python_file` module identity, draft
   deepcopy, finalize warning noise, naming asymmetries).

Items 1-5 are correctness and trust; 6 is leverage (it deletes more code than it adds); 7 is the
new capability; 8-9 are hygiene. After 1-7, the library is what the transcript describes: a
Python-first, validated, notebook-friendly config system where the composed artifact is complete,
typed, and reproducible, with interpolation that pyright can actually check.
