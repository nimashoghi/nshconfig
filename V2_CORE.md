# nshconfig v2 core: drafts, `interp()`, `finalize()`

> **Naming note (as built):** the marker shipped as `interp()` / `Interp` (user decision at
> build time); this spec was written under the working name `derive()`. The mechanical
> replacements below have been applied; semantics are unchanged.


Date: 2026-06-11. The final shape of the v2 core, converged through interactive prototyping and
two multi-agent design panels. This document is the authoritative spec; `V2_DESIGN.md` remains
as the exploration record, and its interpolation mechanisms (`__derive__` hooks at the lowest
common ancestor, `C.inherit` type-anchored markers) are superseded by the single concept here.

Reference implementation: originally prototyped as `v2_prototype/nshv2.py` (now removed;
see git history at the v2 branch root) and since superseded by the real implementation in
`src/nshconfig/`, whose test suite ports every verified claim in this document.

---

## 1. The design in one paragraph

A config field can hold `interp(lambda c: ...)` anywhere a value can sit: assigned to a draft
field, inside a `model_validate` input dict, or as a class default (including inside
`Field(default=...)`). It resolves exactly once, inside pydantic's single validation pass,
reading ancestors (`c.parent`, `c.nearest(Cls)`, always already resolved), the validation root
(`c.root`, raw input plus class defaults, including sibling subtrees), or its own level
(`c.data`). Explicit always beats derived, because presence in the input slot beats the default
slot; last write wins; `del` re-arms. Nothing pending can reach a final config, a dump, an
f-string, or an `if` statement without a loud error naming the dotted path and the lambda's
source line. The whole user-facing vocabulary is three verbs and one value: `Cls.draft()`,
`C.finalize(draft)`, and `interp(...)`.

```python
import nshconfig as C

class LNConfig(C.Config):
    dim: int = 32                  # plain default; leaf classes need no interpolation
    eps: float = 1e-5

class EncoderConfig(C.Config):
    ln: LNConfig

class HeadConfig(C.Config):
    # class-level interpolation: the same kind of value, sitting in the default slot
    dim: int = C.interp(lambda c: c.nearest(ModelConfig).dim)

class ModelConfig(C.Config):
    dim: int = 768
    encoder: EncoderConfig
    head: HeadConfig

class TrainConfig(C.Config):
    batch: int = 8
    model: ModelConfig

# notebook composition: instance-level interpolation, the Hydra move
cfg = TrainConfig.draft()
cfg.model.dim = 1024
cfg.model.encoder.ln.dim = C.interp(lambda c: c.nearest(ModelConfig).dim)  # this tree only
final = C.finalize(cfg)
# final.model.encoder.ln.dim == 1024 (instance marker)
# final.model.head.dim == 1024       (class-default marker, same machinery)
# final.model_dump() is fully concrete; final is frozen and hashable
```

---

## 2. How the design was reached (compressed)

The journey matters because each rejected stage contributed a verified piece of the final shape.

1. **v1's MISSING idiom** (`AllowMissing[T] = MISSING` plus `__post_init__` fill-in) named the
   real need: a regular attribute with a dynamically resolved default. Its boilerplate and
   shallow enforcement were the problems, not the semantics.
2. **`__derive__` hooks at the lowest common ancestor** (first panel winner) made derivation
   plain, type-checked Python, but could not scale to deep trees: the root had to spell out
   every path, and the leaf could not say "my dim follows the model's".
3. **Type-anchored upward markers** (`C.inherit(Anchor).field`) solved the deep-tree case at
   class-definition time, but the author identified the remaining gap: Hydra interpolation is an
   *instance-time* act. You wire a particular tree at composition, not a class at definition.
4. **Validation-time resolution** emerged from studying pydantic's `experimental_allow_partial`
   internals: partial validation itself is trailing-edge streaming and unusable for drafts, but
   the study surfaced the right channel: validators run outside-in (parents before children),
   and a ContextVar stack crosses validation frames. Interpolation moved inside pydantic's own
   validation pass, and a separate resolver engine stopped existing.
5. **The instance-level panel** (five implementing agents, three adversarial judges) converged
   on lambda markers as first-class values, with the decisive realization, confirmed
   mechanically by three independent implementations: once a marker is an ordinary value,
   class-level interpolation is not a feature. It is the same value sitting in the default slot.

Rejected alternatives, with one-line kill reasons (full record in the panel outputs):
typed path proxies (`C.at(Cls).model.dim`): perfect static checking, but cannot express
conditionals or multi-source compute, so lambdas survive as an escape hatch and two mechanisms
ship; OmegaConf-compatible strings (`"${model.dim}"`): refactor-invisible, escaping tax,
class-name matching breaks silently on rename; kept in the drawer as an optional ingestion/CLI
adapter that compiles to `interp`; declarative selectors (`Inherit(ModelConfig)`): pure data and
cycle-proof, but single-source only, and its `then=` escape hatch reintroduces lambdas anyway.

---

## 3. The value model: what `interp()` is

`interp(fn)` returns an `Interp` marker: a tiny frozen object holding the callable and a source
site string (`"<lambda> @ file.py:12"`) captured at construction so errors cite the authoring
line even after cloudpickle. It is **stateless after construction**, so one instance can occupy
a class default slot and any number of instance slots concurrently (verified with one shared
object in both slots resolving identically).

The critical property: **nothing special happens at class definition.** pydantic's metaclass
special-cases exactly one thing in the assignment position, a `FieldInfo` (what `Field()`
returns), absorbing it as field *definition*. Anything else, including an `Interp`, is taken
verbatim as the default *value* (verified: `FieldInfo.default is MARKER`). Consequences, all
verified:

- `a: int = interp(...)` makes the field defaulted, exactly like `a: int = 32`.
- `interp` composes with `Field` because they are different kinds: `a: int =
  Field(default=interp(...), gt=0, description="...")` and
  `b: Annotated[int, Field(multiple_of=5)] = interp(...)` both work.
- The **resolved** value still passes the field's constraints: a parent value violating `gt=0`
  produces pydantic's ordinary greater-than error on the derived field. Interpolation feeds
  validation; it never bypasses it.
- Any explicitly provided value, via constructor kwarg, input dict, or draft assignment, shadows
  the marker completely; the lambda never runs, and the provided value is validated normally.

Legal positions for the marker (one resolution rule covers all): class default slot;
`Field(default=...)`; assigned to a draft field; a value inside a `model_validate` input dict;
a `draft(**kwargs)` seed. Markers may satisfy **required** fields (no default slot needed).

Marker hygiene (each silently-corrupting path was demonstrated before being closed):
`__slots__` so writes into marker-valued objects raise; `__bool__` raises ("refusing to use
pending interp(...) in a boolean context"); `__format__` raises (an f-string would otherwise
bake `interp(<...>)` into a string that validates into a `str` field and survives into a frozen
final); arithmetic raises naturally (`TypeError: unsupported operand`). One documented sharp
edge: `==` is identity (raising would break container membership), so two distinct markers
compare unequal rather than erroring.

---

## 4. Resolution: one rule inside the validation pass

A single `mode="wrap"` model validator on the `Config` base maintains a `ContextVar` stack of
`(model class, in-progress input dict, key label)` as validation descends. Per model, in field
declaration order:

```python
v = value.get(name, field.default)
if isinstance(v, Interp):
    value[name] = v.fn(Ctx(stack))     # exceptions wrapped with path + Cls.field + site
```

That lookup IS the class/instance unification: an instance marker is found at the key; a
class-level marker is the same kind of object found in the default slot when the key is absent.
There is no second mechanism.

Ordering properties (all verified):

- **Each scope resolves before descending**, so ancestor frames (`c.parent`, `c.nearest`)
  always expose resolved values.
- **Same-level chains work by declaration order**: a marker may read an earlier-declared field
  whose value was itself a marker.
- **Sibling subtrees** are reachable root-absolutely (`c.root.model.dim`, Hydra's
  `${model.dim}`): sibling values that are user-set or static defaults are visible in both
  directions, order-free. A sibling value that is *itself still pending* fails loudly with both
  ends named (this is also the cycle detector; see section 6). One pass, no fixpoint, no lazy
  re-entry: a finalized value can never depend on evaluation order invisibly.
- **Re-entrancy guard**: a `model_validate` entered from *inside* a resolver lambda gets a fresh
  stack and its own root sweep, so it cannot silently resolve against the outer tree. This was
  the one attack that defeated all five panel designs before the guard existed.
- **Provenance**: class-default markers injected during resolution are scrubbed from
  `__pydantic_fields_set__` afterwards, so `exclude_unset` dumps keep default provenance, while
  instance markers count as user-set like any other user value.

The `Ctx` API (everything a lambda can see):

| Accessor | Meaning | Sees |
|---|---|---|
| `c.data` | this model's own level | resolved earlier-declared markers, provided values, class defaults |
| `c.parent` | one level up (Hydra `${..x}`) | resolved values; raises "no parent: this model is the validation root" at a root |
| `c.root` | the validation root (Hydra `${a.b}`) | absolute paths incl. sibling subtrees; raw input plus class defaults along the descent |
| `c.nearest(Cls)` | nearest enclosing instance of `Cls` | ancestors only, nominal (`issubclass`); raises with the searched ancestor chain if absent |

Views support exactly one operation, attribute access: provided/resolved value first, class
default second (including `default_factory` with sibling data), nested config dicts wrapped into
sub-views so dotted chains keep working, did-you-mean on unknown fields, loud error on pending
values. Not in the API by design: container element navigation (a list-typed field returns the
raw list), iteration, mutation. Everything a view returns is plain Python, so the lambda body is
just Python: arithmetic, conditionals, `min`/`max`, f-strings over resolved values. Lambdas must
be pure functions of the tree; they run once per finalize.

Precedence needs no rules beyond dict presence, but spelled out as a ladder (every rung
verified):

| rung | source | behavior |
|---|---|---|
| 1 | concrete user value (write, kwarg, dict) | validated as-is; shadows everything below |
| 2 | marker as user value (instance slot) | resolved, then validated; shadows the default slot |
| 3 | marker as class default | resolved, then validated |
| 4 | static default / default_factory | pydantic fills |
| 5 | required and absent | pydantic missing error (per-leaf, with site annotation) |

`del` on a draft pops the instance slot and re-arms whatever is below: a class marker, a static
default, or the missing-required error. Last write wins among user writes, including
marker-then-concrete and concrete-then-marker.

---

## 5. The draft layer

Drafts are **real pydantic instances**, not a parallel object system. `Cls.draft(**seeds)` is
`model_construct` (public API: fills defaults, resolves aliases, initializes privates, sets
`__pydantic_fields_set__` to the provided names) plus a flag, with three attribute dunders
overridden on the base:

- `__setattr__` (draft): unknown name fails at the assignment line with a difflib did-you-mean;
  otherwise a direct `__dict__` write plus `fields_set.add` (provenance is pydantic's own
  mechanism). Never delegates to `BaseModel.__setattr__` for draft field writes, which is
  precisely why the v1 setattr-handler-cache bug class is structurally impossible: the per-class
  memo dict is only populated on the delegation path, and frozen classes never memoize field
  handlers at all (verified against pydantic main.py internals).
- `__getattr__` (fires only for absent fields): auto-vivifies child drafts for plain
  `Config`-typed fields, so `cfg.model.encoder.ln.dim = 64` works on a fresh draft with zero
  ceremony (the v1 pre-assign-a-child-draft pain is gone); marker-default fields raise
  `UnsetError` ("is derived (...); read it after finalize()") BEFORE vivification so a derived
  config-typed field cannot silently shadow its marker; other absent fields raise `UnsetError`
  with the path.
- `__delattr__` (draft): pop value and provenance; re-arm.

`draft()` strips marker defaults that `model_construct` copied into `__dict__` (only non-user-set
ones; `draft(dim=interp(...))` seeds are kept as user provenance). Pending state is visible: the
draft repr labels `[pending: instance interp(<fn @ file:line>)]`, `[pending: class default ...]`,
`<untouched SubConfig>`, and `[UNSET]`, and omits materialized static defaults as noise.

Everything else is pydantic natively: `isinstance(draft, Cls)` is True; equality, deepcopy,
pickle, and cloudpickle are `BaseModel` behavior; repr of finals is pydantic's. Dump methods on
drafts raise `DraftError` loudly (verified necessity: raw pydantic silently emits a partial dump
with zero warnings). Drafts pickle deliberately; work-in-progress travels.

The draft dunders are gated behind `if not TYPE_CHECKING`, so basedpyright keeps pydantic's
declared attribute semantics on drafts: a typo'd draft write is a *static* error. (Ungated, the
`__getattr__` override silently turns every unknown attribute into `Any` and disables checking
across the entire draft layer; this was discovered as a baseline hole and is load-bearing.)

---

## 6. `finalize()` and the failure model

`C.finalize(draft)` is collect-then-validate, nothing else: walk the draft tree into one nested
plain dict (drafts collapse to dicts; untouched **required** sub-config subtrees emit their
recursive spine of `{}` so leaf markers participate and missing-field errors are per-leaf), then
ONE `model_validate`. Resolution happens inside that validation via section 4. `finalize` is
typed `(C) -> C`, idempotent on finals, and non-destructive (the draft stays live:
tweak-and-refinalize is the sweep loop). Finals are frozen via `model_config` (hashable,
immutable; the `ConfigDict` spelling rather than the class kwarg is load-bearing for typing, see
section 7).

The complete failure inventory (each demonstrated):

| Failure | When | Message carries |
|---|---|---|
| Orphan (`nearest` finds no anchor; `parent` at root) | finalize / any validation | dotted path, owning `Cls.field`, marker repr with source line, the searched ancestor chain |
| Cycle (mutual instance markers across subtrees; self-reference) | finalize | one `ValidationError` with both ends as entries, each naming the other's pending marker; the pair is the chain. No recursive resolution exists, so infinite loops are impossible by construction |
| Reading a still-pending sibling | finalize | "X.y is itself pending derivation (...) (possible cycle; set a concrete value, or point both at the same concrete source)" |
| Marker smuggled outside field position (e.g. inside a list under an `Any` field) | root sweep after validation | exact path (`Meta.meta[0]`) and the rule ("markers resolve only as direct values of declared config fields") |
| Pending read on a draft | compose time | `UnsetError` with path and marker site |
| Pending value used as data (`bool`, f-string, arithmetic) | compose time | `DraftError`/`TypeError` naming the marker and its site |
| Draft dump | compose time | `DraftError`: "drafts are not serializable; finalize() first" |
| Resolved value violating field constraints | finalize | pydantic's ordinary constraint error on the derived field |
| Unprovided required field (no marker anywhere) | finalize | pydantic's per-leaf missing error |

Direct construction semantics (the two-path honesty): a standalone `HeadConfig()` is its own
validation root, so a marker needing ancestors fails immediately with the orphan message, which
is correct, since a bare instance cannot know its tree; a marker reading only its own level
(`c.data`) resolves fine standalone; an explicit value always works.

---

## 7. The typing story, honestly

Verified with basedpyright against the prototype:

**Statically checked**: the lambda's declared return type against the field annotation, at the
class-default slot AND the draft-assignment slot (`interp(lambda c: "oops")` into an `int` field
is an edit-time error at both); draft assignment types and attribute names (via the
`TYPE_CHECKING` gating); helper signatures (`def boltz_large(cfg: TrainConfig)`); `finalize`'s
`(C) -> C`; everything in and around the lambda except its body.

**Not statically checkable, loud at runtime instead**: the lambda body (`c` navigations return
`Any`; the author-accepted `default_factory` precedent), with pydantic's validation of the
resolved value as the runtime backstop; anchor reachability (`nearest(ModelConfig)` where none
encloses type-checks, fails at finalize with the chain); which instance is "nearest" (runtime
semantics by definition).

**The contained lies, with their gates**: `interp(...)` claims type `T` while being a marker
(gate: hygiene dunders plus the no-survivors sweep; same precedent as `Field()`); draft mutation
on a frozen-typed class type-checks only because pyright does not enforce
`model_config = ConfigDict(frozen=True)` (only the class-kwarg spelling), which is exactly what
permits the draft idiom; ship a pyright-release canary asserting both halves of that blind spot,
and document basedpyright as the supported checker.

---

## 8. Transport: notebook to cluster

The flagship flow is verified end to end: a **pending** draft whose classes are defined in a
notebook (`__main__`), cloudpickled to a fresh process that has only pydantic, cloudpickle, and
the library, arrives as a draft (`is_draft` True), finalizes there (markers resolve), and dumps
a concrete run record. Marker site strings survive, so cluster-side errors cite notebook lines.

Two hardening requirements were discovered empirically and are mandatory:

1. The wrap validator's body must be a **module-level function** (attached via
   `model_validator(mode="wrap")(classmethod(...))`). A class-body def is cloudpickled by value
   for notebook-defined subclasses and drags in the module `ContextVar`, which is unpicklable.
2. pydantic-core `SchemaValidator`/`SchemaSerializer` need a `copyreg` lazy stand-in
   (`_LazyValSer`): marker lambdas whose `__globals__` forward-reference sibling model classes
   create pickle cycles in which cloudpickle reconstructs a validator from a partially
   materialized shared core-schema dict (observed `SchemaError ... KeyError: 'function'`).
   Deferring construction to first attribute access fixes it. This is properly an upstream
   pydantic fix; v2 ships it as a contained shim and should file the issue.

Policy: pickles are transport, JSON is the record. Finals dump concrete values only (structural
guarantee: nothing symbolic survives section 6's sweep). Pending drafts have no textual
serialization, deliberately; if "serializable pending configs" ever becomes a need, the panel's
string-form work (OmegaConf-style templates compiling to `interp` markers, plus
`dump_draft`/`load_draft`) is the designed-but-not-shipped adapter.

---

## 9. Hydra/OmegaConf parity map

| Hydra/OmegaConf | v2 | Notes |
|---|---|---|
| `${..dim}` (relative parent) | `c.parent.dim`, or `c.nearest(Cls).dim` | `nearest` is the restructure-proof form: anchored on meaning, not hop count; disambiguates teacher/student nesting per-subtree (verified) |
| `${model.dim}` (root absolute) | `c.root.model.dim` | sibling subtrees reachable; raw input + class defaults |
| `${oc.env:...}`, custom resolvers | the lambda body is Python | no resolver registry; environment access is a visible `os.environ` read in code (discouraged in favor of explicit fields) |
| interpolation in YAML at compose time | `interp(...)` assigned at compose time | the instance-level act, on drafts or dict input |
| config groups / defaults lists | plain functions mutating drafts | `def boltz_large(cfg): ...` |
| lazy resolution at access time | rejected | resolve-once-at-finalize; a run record is dead data |
| unresolved `${...}` persisting in saved YAML | rejected (drawer: string adapter) | finals are concrete by construction |
| `--set a.b=${x.y}` CLI overrides | drawer | designed as strings-compile-to-derive, not shipped |

The deliberate expressiveness limit versus OmegaConf: one resolution pass. Cross-sibling
derived-to-derived chains error loudly instead of resolving lazily (same-level chains and
ancestor chains work). The panel's string-form agent proved demand-driven view resolution with a
seen-set lifts this if real usage demands it; it is an additive change to `_View`, not a
redesign.

---

## 10. Implementation inventory

`v2_prototype/nshv2.py`, ~420 lines total, structured as:

| Piece | LOC | Role |
|---|---|---|
| `Interp` + `interp()` | ~45 | the value: fn, site, hygiene dunders, typed factory |
| `_View` + `Ctx` | ~90 | navigation views, did-you-mean, pending-read guard |
| `_interpolation_scope` | ~45 | the wrap validator: stack, labels, one-rule resolution, re-entrancy guard, fields_set scrub, root sweep trigger |
| `_assert_no_pending` | ~18 | the no-survivors sweep |
| `_LazyValSer` + copyreg | ~35 | transport shim (upstream-fixable) |
| `Config` base | ~120 | frozen finals, draft(), labeled repr, gated dunders, dump gates |
| collect + spine + `finalize` | ~35 | the boundary |

Pydantic touchpoints, all public or documented surface: `model_construct`, `model_fields` /
`FieldInfo.default` / `get_default`, `__pydantic_fields_set__`, `model_validator(mode="wrap")`,
`model_validate`, `ConfigDict(frozen=True)`. Floor: pydantic >= 2.13 (the 2.12
cloudpickle/after-validator silent-`{}` bug, found independently twice in earlier panels, plus
before/wrap-validators-only discipline in the base), Python >= 3.10.

What this core makes unnecessary, relative to v1 and to earlier v2 drafts: `MISSING` /
`AllowMissing` / `validate_no_missing` (a derived default is a marker; required-set-later is a
draft hole); `__derive__` hooks and their iterate-to-agreement scheduler (rules live on fields
as values); `C.inherit` anchor classes and the parent-map resolver (ancestor access is the
validator stack); the Registry, codegen, file-format helpers, Mapping facade, and Singleton (per
`V2_DESIGN.md` sections 8 and 9, unchanged).

---

## 11. Open knobs

1. **The name.** `derive` is the entire API surface; candidates were `from_`, `interp`,
   `value`. `FromParent` was retired once markers could read root and siblings.
2. **Cross-sibling chains.** Keep the one-pass restriction (loud, both ends named) or adopt
   demand-driven view resolution (proven, costs a memoization layer and makes evaluation order
   less obvious). Recommendation: keep one-pass until a real config hits it.
3. **`==` on markers.** Identity, not an error; raising would break container membership.
   Documented sharp edge.
4. **Container navigation in views** (`c.root.layers[0].dim`): undesigned; lists return raw.
   Decide when a real config needs it.
5. **The drawer adapters**: string-template ingestion and `--set` CLI overrides compiling to
   `derive`; `dump_draft`/`load_draft` for serializable pending trees. All designed and
   prototyped by the panel; none shipped until demanded.
6. **Upstream**: file the pydantic-core pickle-cycle issue (`_LazyValSer`'s reason to exist)
   and the 2.12 cloudpickle/after-validator bug if not already fixed upstream.
