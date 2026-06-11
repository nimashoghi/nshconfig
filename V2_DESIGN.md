# nshconfig v2: design brainstorm

> **Status note (2026-06-11, later the same day):** the interpolation and derivation mechanisms
> in this document (sections 3-4: `__derive__` hooks at the LCA, `C.inherit` type-anchored
> markers, the iterate-to-agreement scheduler) were superseded by a single unified concept,
> `derive()` markers as first-class field values resolved inside pydantic's validation pass.
> See `V2_CORE.md` for the authoritative spec and `v2_prototype/nshv2.py` for the verified
> implementation. The deletions (section 9), the everything-in-Python story (section 10), and
> the engineering substrate (section 11) remain current.

Date: 2026-06-11. A from-scratch redesign; no backwards compatibility with v1. Target: pydantic
>= 2.13, Python >= 3.10, basedpyright as the supported type checker.

Method note: this document synthesizes a multi-agent design exploration run against this repo.
Nine design agents developed competing mechanisms (two built working prototypes and verified
their claims live against pydantic 2.12.5/2.13.4), and three adversarial judges scored everything
under type-safety, reliability, and simplicity lenses. Claims marked "verified" were exercised in
running code during that process, not assumed. Where the judges disagreed, this document says so
and takes a position.

---

## 1. What v2 is

A library that does exactly three things well, for one audience (ML researchers storing training
hyperparameters), with one design value (radical simplicity, every abstraction pays rent):

1. **Draft building**: construct a config by plain Python assignment, validation deferred to one
   explicit boundary.
2. **Derived values** (the Hydra-interpolation replacement): "diffusion.dim follows trunk.dim
   unless I say otherwise", written as ordinary typed Python, resolved at the boundary, gone from
   the final artifact.
3. **Frozen, complete finals**: the thing that comes out of the boundary is an immutable,
   hashable, fully-concrete pydantic model that pickles, dumps, and reproduces.

Everything else from v1 is deleted (section 9) or becomes a documentation page.

The whole user-facing vocabulary is four names and one rule:

- `Cls.draft()` makes a draft; `C.finalize(draft)` makes a frozen final; `def __derive__(self)`
  declares derived values owned by a class; `C.inherit(Anchor).field` declares, at a leaf field,
  a value located by searching up the tree at finalize (section 3.5).
- The one rule: **explicit assignments always beat derived values.**

`MISSING` does not exist in v2. A "required, set later" field is just an unset field on a draft
(finalize fails loudly if it stays unset). A "dynamically resolved default" is one line in
`__derive__`. The placeholder object, the `AllowMissing` annotation, the typing trick, and the
no-missing walk all dissolve.

---

## 2. The scenario, end to end

```python
# myproj/experiments/boltz.py
from __future__ import annotations

import nshconfig as C


class TrunkConfig(C.Config):
    dim: int = 768
    num_heads: int = 12
    head_dim: int  # derived below

    def __derive__(self) -> None:
        # Plain Python on real values. basedpyright checks every line.
        # Assignments in a derive pass are soft: they fill only fields
        # the user never set.
        self.head_dim = self.dim // self.num_heads


class DiffusionConfig(C.Config):
    dim: int       # derived by whichever parent mounts this
    cond_dim: int  # derived by whichever parent mounts this
    steps: int = 100


class BoltzConfig(C.Config):
    trunk: TrunkConfig
    diffusion: DiffusionConfig
    seed: int = 0

    def __derive__(self) -> None:
        # Cross-module rules live at the lowest common ancestor: the only
        # class that owns both subtrees, so every reference here is plain,
        # typed attribute access. Renaming trunk.dim breaks this line in
        # the editor, not at runtime.
        self.diffusion.dim = self.trunk.dim
        self.diffusion.cond_dim = 2 * self.trunk.dim


def boltz_large(cfg: BoltzConfig) -> None:
    """A Hydra config group is just a function that mutates a draft."""
    cfg.trunk.dim = 1024
    cfg.trunk.num_heads = 16
```

```python
# notebook
cfg = BoltzConfig.draft()       # typed as BoltzConfig; nested drafts auto-vivify
boltz_large(cfg)                # helper mutates the draft
cfg.diffusion.dim = 512         # explicit override, recorded as user-set

final = C.finalize(cfg)         # derive pass, then ONE model_validate, then freeze

assert final.trunk.dim == 1024
assert final.trunk.head_dim == 64        # 1024 // 16, followed boltz_large
assert final.diffusion.dim == 512        # the override won
assert final.diffusion.cond_dim == 2048  # still tracked trunk.dim
final.model_dump_json()                  # concrete values only, no residue

del cfg.diffusion.dim                    # forget the override...
assert C.finalize(cfg).diffusion.dim == 1024   # ...derivation applies again
```

Failure behavior (all of these were demonstrated by the prototype):

- `steps` required and never set: pydantic's own `ValidationError` at finalize, per-leaf
  (`diffusion.steps: Field required`), annotated by v2 with `never assigned on this draft` or
  `last assigned at train.py:54`.
- Two hooks that need each other's outputs: `DeriveCycleError` at finalize naming every blocked
  hook and the field it waits on.
- Typo on a draft (`cfg.trunk.dmi`): `AttributeError` at the assignment line with a did-you-mean
  suggestion, not at finalize.
- `cfg.model_dump()` on a draft: refused loudly ("drafts are not serializable; finalize() first").
  Pickle/cloudpickle of drafts is deliberately allowed (work-in-progress travels to and from
  notebooks; assignment sites travel with it).

Note also what did NOT happen above: nobody ever wrote `cfg.diffusion = DiffusionConfig.draft()`.
In v1, helpers that edited a nested config crashed with `AttributeError` unless the caller
remembered to pre-assign a child draft for every required sub-config; in v2, `cfg.diffusion`
auto-vivifies a child draft node on first access (section 4), so helpers can reach arbitrarily
deep (`cfg.a.b.c.dim = 1`) into a fresh draft with zero ceremony.

---

## 3. Interpolation: the `__derive__` hook

### 3.1 Why a hook and not expression objects

The design panel converged on two finalists. The losing one deserves a fair description because
it nearly won (section 8.1). The winner:

**Derived values are ordinary Python statements in a `__derive__` method, executed once at
finalize against materialized draft values, under soft-assignment semantics.** No expression
AST, no proxy objects, no lambdas, no sentinels, no string paths. The mechanism is, deliberately,
v1's own `__post_init__`-fills-MISSING idiom with the boilerplate deleted: the `if x is MISSING:`
guards become automatic (soft assignment), the MISSING sentinel becomes unnecessary (fields are
simply unset until something sets them), and the scheduling is made sound (section 3.3).

What this buys, concretely:

- **The full language, fully type-checked.** `self.diffusion.dim = self.trunk.dim` is plain
  attribute access on declared fields: renames are edit-time errors, arithmetic has real types,
  and crucially, conditionals, `min`/`max`, f-strings, and method calls just work and are
  checked. Every expression-object design in the panel could type only attribute paths plus
  arithmetic; anything else either raised at runtime despite type-checking, or escaped into a
  lambda hatch, which is the thing this redesign was asked to avoid. With a hook there is no
  escape hatch because Python is the language.

  ```python
  def __derive__(self) -> None:
      if self.trunk.dim > 512:           # branching on a real value: just Python
          self.diffusion.steps = 200
      self.run_name = f"boltz-d{self.trunk.dim}"
  ```

- **One rule for overrides, zero API.** User assignments are hard and recorded as provenance;
  derive-pass assignments are soft (they fill only fields with no value and no user provenance).
  "Explicit beats derived" needs no flags, no modes, no per-field markers. `del` removes a value
  and its provenance, re-arming derivation. Assigning a constructed sub-config
  (`cfg.trunk = TrunkConfig(dim=512)`) marks exactly its `model_fields_set` as user-set, so
  `trunk.dim` is protected but `trunk.num_heads` (left to default) stays derivable. Verified.

- **Rules live where both sides are in scope.** A class derives its own fields from its own
  fields; cross-subtree rules go on the lowest common ancestor. This is the same shape as the
  "validators at the top level" pattern the author already uses for invariants, and a `__derive__`
  rule can never reach outside the class that declared it. Downward-only hooks do not scale to
  deep trees on their own (the root would have to spell out every path); that is what the
  upward-reference layer in section 3.5 exists for. The two compose: hooks push down,
  `inherit` defaults pull up, and both resolve in the same scheduler.

- **Two pydantic touchpoints, both public.** The whole mechanism reads `model_fields` metadata
  and calls `model_validate` once. No `FieldInfo` retrofits, no `model_rebuild` registries, no
  schema participation, nothing internals-adjacent (compare section 8.1).

The known cost, stated honestly: the derivation is not visible at the field declaration site.
`DiffusionConfig.dim` reads as a bare `dim: int`; you must look at the mounting class's
`__derive__` to learn its effective default. Mitigations: it is one greppable method per class, a
docstring convention on the field ("derived by parent"), and the panel's agreed upgrade path if
this ever hurts at scale: an additive declarative recorder that compiles to a generated
`__derive__` (section 8.1), which would not change the runtime model. Do not build that in v2.0.

A second honest cost: derived defaults exist only on the draft/finalize path. Section 3.6
specifies exactly how direct construction behaves and the escape valves.

### 3.2 Semantics

- A hook is `def __derive__(self) -> None`, resolved through the MRO (subclasses inherit and may
  extend via `super().__derive__()`).
- During the derive pass, `self` is the draft node for that class, typed as the class. Reads
  return: the stored value, else the static field default, else raise `UnsetError` (which defers
  the hook, see 3.3). Reads never return placeholder objects: helpers and hooks can branch on
  real values.
- Writes during the pass are soft: skipped if the target is user-set or already materialized.
  Writes outside the pass (user code on drafts) are hard and provenance-recorded.
- Hooks must be pure functions of the config state: read fields, write fields, nothing else. The
  scheduler may run a hook more than once (3.3) and the double-run makes impurity loud.

### 3.3 The scheduler, and the one hazard that had to be fixed

The naive scheduling of imperative hooks has a verified silent failure mode. In the panel's
prototype, hooks ran children-first with "freeze-on-read" (reading an unset-with-default field
pins the default so the snapshot stays consistent). The reliability judge then demonstrated:
a child hook reads `self.dim` (pins default 768), the parent hook later derives
`trunk.dim = 1024` into it, the soft write silently no-ops, and the final config carries 768 with
no error anywhere. Temporal scheduling plus silent no-ops is a wrongness generator; the fix must
make every ordering problem loud or gone.

v2's scheduler: **iterate to agreement.**

1. A pass runs every hook in deterministic pre-order (root first, then children, stable field
   order). Within a pass, a hook that reads an unset required field raises `UnsetError` and is
   deferred to the end of the pass queue; a pass with deferred hooks and no progress fails
   immediately with `DeriveCycleError` naming each blocked hook and the path it waits on.
2. After a pass completes, run another full pass against the now-filled tree, with all
   derived-but-not-user-set values cleared first. If pass N produces exactly the values of pass
   N-1, the derivation has converged: done.
3. If values still differ after `len(hooks) + 2` passes, raise `DeriveCycleError` listing the
   oscillating paths.

Properties: any stale-default read in pass 1 is corrected by pass 2 (the re-derivation sees the
parent's fill); a genuine order dependency converges in as many passes as the dependency depth; a
genuine cycle oscillates or stalls and is reported with the paths involved; and a hook that is
not a pure function of config state produces pass disagreement, which is also loud. The invariant
to advertise: **finalize either returns values consistent with every rule and every read, or it
raises naming the paths that could not be made consistent. There is no third outcome.**

This scheduler is the highest-risk piece of v2 (an estimated ~150-200 LOC) and the first thing to
prototype; the panel validated single-pass and deferral mechanics in running code, and the
fixpoint loop is the planned extension of that prototype, not yet built.

### 3.4 The "one knob, many followers" pattern

The panel explored a dedicated typed-variables mechanism (`class Dims(C.Vars): model_dim: int`
with scoped providers, section 8.2) and concluded the workflow it serves is expressible with zero
machinery: a plain field on the root plus root rules.

```python
class BoltzConfig(C.Config):
    model_dim: int = 768            # THE knob; documented tunable surface
    trunk: TrunkConfig
    diffusion: DiffusionConfig

    def __derive__(self) -> None:
        self.trunk.dim = self.model_dim
        self.diffusion.dim = self.model_dim
        self.diffusion.cond_dim = 2 * self.model_dim
```

`cfg.model_dim = 1024` in a notebook now retunes the whole tree, while any individual
`cfg.trunk.dim = 512` still wins locally. This becomes a documented pattern, not a feature.

### 3.5 Reaching up: type-anchored inheritance for deep trees

`__derive__` at the lowest common ancestor solves the easy cases. It does not solve the case
that real configs are made of, named explicitly in the original design conversation: a large,
deeply nested tree where a leaf's value follows an ancestor's knob, Hydra's
`dim: ${...dim}` (relative) and `dim: ${model.dim}` (global). With hooks alone, `ModelConfig`
would have to spell out `self.encoder.ln.dim = self.dim`, `self.decoder.ln.dim = self.dim`, and
every other path, for every LN in every block of every layer. The knowledge "my dim follows the
model's dim" belongs to the leaf; the leaf must be able to say it.

The mechanism: hierarchy exists in v2 wherever a rooted draft is being finalized (drafts own
their children, so finalize can build a parent map by one walk; no parent pointers are ever
stored on instances, so no pickle cycles and no aliasing headaches at rest). On top of that, a
leaf field declares an upward reference as its default:

```python
class DimSource(C.Config):          # the anchor: a tiny base/mixin in a leaf module
    dim: int = 768

class LNConfig(C.Config):
    dim: int = C.inherit(DimSource).dim   # "nearest enclosing DimSource decides"
    eps: float = 1e-5

class EncoderConfig(C.Config):
    ln: LNConfig

class ModelConfig(DimSource):       # provider = "I am the nearest DimSource above my subtree"
    encoder: EncoderConfig
    decoder: DecoderConfig

class TrainConfig(C.Config):
    model: ModelConfig
    lr: float = 1e-4
```

```python
cfg = TrainConfig.draft()
cfg.model.dim = 1024                 # one knob
cfg.model.decoder.ln.dim = 64        # one explicit leaf override
final = C.finalize(cfg)
# encoder.ln.dim == 1024 (flowed three levels down), decoder.ln.dim == 64 (override won)
```

This entire flow, including the failure modes below, was prototyped live on the live-draft base
against this repo's venv; every claim here ran.

**Why anchor by TYPE rather than by path or hop count.** Hydra's `${...dim}` encodes "three
levels up", which breaks the moment a subtree is nested one level deeper; `${model.dim}` encodes
one absolute root shape, which breaks the moment the subtree is mounted under a different root.
`C.inherit(DimSource).dim` encodes the actual dependency, "the nearest enclosing thing that owns
a model dim", which survives both refactors, and disambiguates the case both Hydra forms fumble:

```python
class Distill(C.Config):
    teacher: ModelConfig
    student: ModelConfig
# teacher.dim = 1024, student.dim = 256:
# teacher's LNs resolve to 1024 and student's to 256, each leaf binding to ITS nearest
# ModelConfig. Verified.
```

Resolution semantics, precisely:

- An `inherit` default behaves exactly like a static default, except its value is located at
  finalize by walking up the parent map to the nearest `isinstance` match of the anchor, then
  following the (multi-hop allowed) attribute path: `C.inherit(TrainConfig).model.dim` is the
  global form when root-anchoring is genuinely wanted. The precedence ladder needs no new rung:
  inherit default < `__derive__` writes < user-set, and the first two fall out mechanically (a
  hook write fills the field, so the marker never resolves; a user write is provenance-protected
  from both).
- Resolution participates in the same iterate-to-agreement rounds as `__derive__`: a pull whose
  source is itself unresolved (a provider whose own value is an `inherit` from higher up) defers
  and lands next round; verified with a two-level chain. A leaf `__derive__` that computes from
  an inherited sibling (`head_dim = self.dim // self.num_heads` where `dim` is inherited) is the
  same deferral, which is why pulls carry no arithmetic: reads-plus-hooks already compose.
- Before resolution, finalize vivifies the required sub-config spine (untouched subtrees must
  exist for their leaf markers to participate; verified as the first prototype failure and fix).
- On drafts, a pending `inherit` field reads as `UnsetError` naming the marker, never as the
  marker object: marker-valued defaults are stripped from `__dict__` at draft creation, and
  `del` re-arms them like any default.
- Failure is loud and named: no enclosing anchor yields
  `ln.dim: no enclosing DimSource (<inherit DimSource.dim>)` at finalize, with the full leaf
  path; cycles and never-resolved chains surface through the scheduler's no-progress report. A
  draft node reachable through two parents has no well-defined "up"; the parent-map walk
  detects aliased mounts and errors when an upward reference needs them (the same single-parent
  rule OmegaConf enforces, but explicit).

**The typing story, honestly** (the user's framing: not fully type-checked is fine, done
tactfully). What basedpyright checks, verified: the anchor's field names and types through the
proxy (`C.inherit(ModelConfig).dimm` and assigning `inherit(...).dim` to a `str` field are
edit-time errors, so renames on the PROVIDER side break every pull in the editor), and the
assignment-site/annotation compatibility. What it cannot check: that an enclosing anchor will
exist at finalize (runtime, loud, named), and which instance is "nearest" (runtime semantics by
definition). The pull proxy records attribute paths only, no operators, so the
expression-algebra dunder minefield from section 8.1 stays out: anything computed belongs in a
hook, where it is plain checked Python.

**The import-direction problem, and the anchor idiom.** A leaf module usually cannot import the
concrete provider (`ln.py` importing `model.py` is a cycle, since modules import downward). The
idiom is the one shown above: a tiny anchor base class (`DimSource`) living leaf-ward, imported
by both sides; providers opt in by inheriting it. This is dependency injection with config
classes as both providers and keys, no new Vars machinery, and it doubles as documentation (the
anchor class IS the declared tunable surface). Where the concrete class is importable without a
cycle (same module, or anchoring on the root from composition-layer code), `C.inherit(That)`
works directly.

**The provider-side dual, for leaves you do not own.** When the leaf class cannot be edited
(third-party configs), the broadcast form covers the same wiring from above, fully typed with no
cast anywhere (verified):

```python
class ModelConfig(C.Config):
    dim: int = 768
    def __derive__(self) -> None:
        for ln in C.descendants(self, LNConfig):   # Iterator[LNConfig], honestly typed
            ln.dim = self.dim                      # soft: per-leaf overrides still win
```

Own the leaf: pull. Own the parent: push. Own both: taste; the pull keeps the rule on the field
it describes.

### 3.6 Direct construction and `__derive__`

`__derive__` runs only inside `C.finalize`. Direct construction is plain pydantic, untouched:

- `TrunkConfig()` fails **statically**: pydantic's synthesized `__init__` makes `head_dim` a
  required parameter, so basedpyright reports `Argument missing for parameter "head_dim"` in the
  editor (verified). Forced at runtime, it is pydantic's own `ValidationError: head_dim Field
  required`. The constructor signature tells the truth: this class does not know its derived
  values without the finalize pass.
- `TrunkConfig(dim=1024, num_heads=16, head_dim=64)` works and derivation never runs; fully
  explicit construction is fully explicit.
- `DiffusionConfig()` likewise demands `dim`/`cond_dim`, which is not a wart but the honest
  answer: those derivations live on `BoltzConfig`, and a bare `DiffusionConfig` genuinely cannot
  know `trunk.dim`. A sub-config whose derived fields are filled by an ancestor stays genuinely
  required when used alone; never a silent hole.
- `inherit`-defaulted fields (3.5) are the one direct-construction case the checker cannot flag:
  `LNConfig()` typechecks (the field has a default) but the marker cannot know its ancestor
  outside a tree, so `validate_default` rejects it at construction with a message naming the
  marker and pointing at the draft path (or passing the field explicitly: `LNConfig(dim=64)` is
  fine). Loud, but runtime-only; recorded in the section 7 ledger.

Why not run self-scoped hooks inside constructors? Three reasons, in increasing order of force:
it would split hooks into two castes (self-scoped rules run everywhere, ancestor rules only at
finalize), it would move derivation onto every validation event instead of one boundary, and
mechanically there is no sound place for it: a `mode="before"` validator sees raw unvalidated
input (deriving from `"768"` the string), and a `mode="after"` validator never runs when a
required field is missing, so making it work would require a placeholder default, which is
MISSING reborn. The two-path asymmetry is the cheapest honest design.

Escape valves, in preference order:

1. `C.finalize(TrunkConfig.draft())` is the one-liner for "a fully derived standalone instance";
   `TrunkConfig.draft(dim=1024)` seeding makes it one line with overrides.
2. A class whose derivations are purely self-contained may instead declare them as stock pydantic
   data-aware defaults, `head_dim: int = Field(default_factory=lambda data: data["dim"] //
   data["num_heads"])`, which makes `TrunkConfig()` work everywhere at the cost of a stringly,
   unchecked rule body. Pick one mechanism per field, never both.

Two consequences worth naming. First, `trunk: TrunkConfig = TrunkConfig()` as a field default
would crash at class-definition time (it is a direct construction); leave sub-config fields bare
(`trunk: TrunkConfig`), which costs nothing because drafts auto-vivify them and finalize emits
`{}` for untouched ones. Second, assigning a constructed instance into a draft
(`cfg.trunk = TrunkConfig(dim=512, num_heads=8, head_dim=64)`) necessarily passes every required
field, so all of them carry user-set provenance and derivation will not rewrite any of them;
assign fields individually (or seed via `draft(**kwargs)`) when you want derivation to keep
following.

---

## 4. The draft layer: live pydantic instances, not a parallel system

Revised after a source-level study of pydantic v2.13.4 (the `pydantic/` Python package and the
merged `pydantic-core/` Rust crate). An earlier revision of this document made drafts a parallel
shadow-tree object on the grounds that "any design that mutates real pydantic instances collides
with the private per-field setattr handler cache". The study shows that claim was too broad, and
the precise mechanics rehabilitate a much better design: **drafts are real pydantic instances.**

The exact cache mechanics (`pydantic/main.py`, `__setattr__` at ~1044 and `_setattr_handler` at
~1052, v2.13.4): `BaseModel.__setattr__` consults a per-class memo dict
(`__pydantic_setattr_handlers__`) and, on miss, picks a handler by reading `model_config` at that
moment, then memoizes it. v1 broke because it flipped `validate_assignment` in `model_config`
mid-flight, so the non-validating handler got memoized class-wide. Two consequences for v2:

- A `Config.__setattr__` override that handles draft writes itself (direct `__dict__` write plus
  `__pydantic_fields_set__.add`) and never delegates to `BaseModel.__setattr__` for draft field
  writes never touches the memoization path at all. Verified: after arbitrary draft assignments,
  the class's handler cache contains only the (correct, harmless) private-attribute handlers.
- Frozen classes never memoize field handlers in the first place: `_check_frozen` raises before
  handler selection. Finals are therefore safe even on the delegation path. Verified.

So the v1 hazard is not "mutating pydantic instances"; it is "mutating class-level config". The
revised draft layer mutates neither.

### 4.1 Mechanism

A draft is `cls.model_construct(**values)` (public API, stable since 2.0: fills defaults
including data-aware factories, resolves aliases, initializes private attributes, sets
`__pydantic_fields_set__` to exactly the provided names) plus one private flag, with three
attribute dunders overridden on the `Config` base:

- **`__setattr__`** (draft): name not a field -> `AttributeError` with a difflib did-you-mean at
  the offending line; otherwise direct `__dict__` write, `__pydantic_fields_set__.add(name)`
  (provenance is pydantic's own mechanism, not a parallel set), and a `file:line` site record
  into a `PrivateAttr` dict. Non-draft: delegate to pydantic, whose frozen check rejects writes
  to finals. Private attributes flow through pydantic's private handler on both paths (verified:
  privates are writable on frozen instances, which is also what makes the draft flag itself
  settable).
- **`__getattr__`** (only invoked when a field is absent from `__dict__`, i.e. required and
  unset): chain to pydantic first (privates, extras); then, on a draft, auto-vivify a child
  draft for plain `Config`-typed fields and store it in `__dict__` WITHOUT adding it to
  `fields_set` (present, but not user-set), so `cfg.diffusion.cond_dim = 2` works on a fresh
  draft with zero ceremony; otherwise raise `UnsetError` with the dotted path. Union-typed
  sub-configs (`AdamConfig | SGDConfig`, conservatively also `Sub | None`, whose default
  `None` is materialized by `model_construct` anyway) require explicit assignment, because
  vivification would silently pick a union branch; the error says exactly that.
- **`__delattr__`** (draft): drop from `__dict__` and `fields_set`; the static default or
  derivation applies again at the next finalize. (Without the override, pydantic's frozen check
  rejects `del` on drafts; verified.)

Everything else IS pydantic, with zero owned code: `isinstance(draft, BoltzConfig)` is **True**;
repr renders partial drafts correctly for free; equality, `deepcopy`, pickle and cloudpickle
(work-in-progress travels between notebook and cluster, sites included) are `BaseModel`
natives; defaults materialize through `FieldInfo.get_default`; treescope/rich integration sees
an ordinary model. The whole layer was prototyped live against this repo's venv: nested edit on
a fresh draft, provenance via `fields_set`, frozen finals rejecting writes with a clean handler
cache, per-leaf missing errors, `del` re-arming, pickle/deepcopy round-trips all verified.

Two gates remain owned, both loud: dump methods on drafts raise `DraftError` (verified that raw
pydantic otherwise silently emits a partial dump with no warning: `{'seed': 0}` from an empty
draft); and `C.finalize`/`C.thaw`/`C.is_draft` stay module functions so the instance namespace
remains reserved for user fields (`Cls.draft()` is a classmethod; class namespace is safe).

One documented caveat inherited from `model_construct`: a user-declared data-aware
`default_factory` that references a field defined after it raises a raw `KeyError` at draft
creation (pydantic's documented ordering rule, surfacing earlier than usual). Loud, at the
`draft()` call.

Container fields holding sub-configs (`layers: list[LayerConfig]`) hold ordinary lists whose
elements may be drafts or finals; finalize's collect walk recurses into list/dict values and
collapses any draft it finds. In-place element thawing sugar (`cfg.layers[0].dim = 64` on a
final element) is deferred; replace the element with a draft for now.

### 4.2 What `experimental_allow_partial` actually is, and the reuse verdict

Since the goal ("validate incomplete data") sounds aligned, v2's draft layer was checked against
pydantic's partial validation at the source level rather than the docs level. Anatomy (v2.13.4):

- Surface: `TypeAdapter.validate_python/json/strings` only (`type_adapter.py:407,447`); the flag
  is passed straight through to pydantic-core as a jiter `PartialMode`.
- Core: `ValidationState.allow_partial` (`validators/validation_state.rs`), whose own comment
  defines the scope: "True if `allow_partial=true` and we're validating the **last element** of a
  sequence or mapping."
- Sequences (`input/return_enums.rs:134-156`): only the final element keeps partial mode;
  validation errors there are swallowed and the element is dropped (`if !is_last_partial {
  errors.extend(...) }`). Everything earlier validates normally.
- TypedDicts (`validators/typed_dict.rs:158-218`): partial stays active only for the field whose
  lookup key equals the input's literal last key (`dict.last_key()`). Missing required fields are
  never waived; only `NotRequired`/`total=False` fields may be absent.
- Models (`validators/model_fields.rs:139-140`): `// this validator does not yet support partial
  validation, disable it to avoid incorrect results` followed by `state.allow_partial =
  false.into()`. Models opt out wholesale.

Verdict: partial validation is a **streaming-truncation** feature (validate the prefix of an
LLM/JSON stream, drop the ragged trailing edge). Drafts need the opposite invariants: holes
anywhere (not just trailing), holes that persist and are reported (not dropped), mutation, and
provenance. Even when the model validator's "not yet" lands upstream, the semantics will remain
trailing-edge by construction (the whole feature rides jiter's `PartialMode`). Nothing to reuse
here, and the right relationship is to watch it, not build on it.

The aligned-goals instinct still lands, just one layer down: the machinery v2 now reuses is
`model_construct` (partial instances with defaults and provenance), `__pydantic_fields_set__`
(override-vs-derive provenance), `ConfigDict(frozen=True)` (finals), and one `model_validate`
(the boundary). The draft layer's owned code shrinks to the three dunder overrides, the collect
walk, and the gates: roughly 150 lines, none of them a parallel object model.

---

## 5. The finalize boundary

`C.finalize(draft, *, strict=None, context=None)` does, in order:

1. **Derive**: the scheduler of section 3.3.
2. **Collect**: walk the tree into one nested plain dict, collapsing draft children to dicts
   (so nothing slips through by instance identity) and recursing into list/dict values. For
   required-but-untouched sub-config fields, emit `{}` so pydantic reports per-leaf errors
   (`diffusion.steps: Field required`) instead of per-subtree (`diffusion: Field required`).
   Verified difference in error quality.
3. **Validate**: exactly one `Cls.model_validate(data, strict=..., context=...)`. There is no
   other validation anywhere in the lifecycle, which structurally deletes v1's lax-init versus
   strict-finalize incoherence. `context` is the sanctioned channel for external knobs (cluster
   name, data root) reaching user validators without polluting the schema.
4. **Annotate failures**: a `ValidationError` is wrapped in `BuildError`; each error `loc` is
   joined against the sites index, yielding `last assigned at train.py:54` or `never assigned on
   this draft`. Pre-validation failures (cycles, stalls) collect ALL problems into one report
   rather than dying on the first.
5. **Stamp and freeze**: the result is a normal pydantic instance of a class whose
   `model_config = ConfigDict(frozen=True)`. Because collect feeds `model_validate` a fully
   materialized dict, pydantic would record every field as "set"; finalize therefore restores
   each node's `__pydantic_fields_set__` from the draft's provenance afterwards (a plain
   object-level write, pickled natively by pydantic), so explicit-vs-derived survives on the
   final with zero parallel bookkeeping.

Lifecycle properties:

- finalize is **non-destructive** (the draft stays mutable; tweak-and-refinalize is the sweep
  loop) and **idempotent** (`C.finalize(final)` is identity, so boundary code can call it
  defensively).
- `C.thaw(final)` returns a fresh draft seeded only from the restored `model_fields_set`
  (mechanically: `model_construct(_fields_set=...)` over the user-set values, recursively), so
  "load yesterday's config, bump `trunk.dim`, refinalize" re-derives dependent values instead of
  freezing them at stale numbers. Invariant: `C.finalize(C.thaw(x)) == x` when nothing changed.
  Thawing a config whose provenance is total (validated from JSON by hand, where every present
  key counts as set) warns loudly that derivations will not re-arm rather than silently behaving
  differently.
- `final.replace(**updates)` is a thin validated wrapper (verified: pydantic's `model_copy` and
  `__replace__` both skip validation and happily store garbage), implemented as
  thaw-update-finalize so derivations follow the update. `replace(model_dim=1024)` is the
  one-line sweep idiom.
- Frozen finals are hashable (pydantic native), dict-keyable, equal by value, and pickle cleanly
  including from notebooks (cloudpickle by-value, verified end-to-end including nested classes).

Why frozen via `model_config = ConfigDict(frozen=True)` and never the `frozen=True` class kwarg:
verified pyright asymmetry. Pyright enforces frozen-ness for the class-kwarg spelling and would
therefore reject every draft assignment (drafts are typed as the class); it does not enforce the
`model_config` spelling, which keeps draft mutation type-legal while runtime frozen-ness protects
finals. This blind spot is load-bearing: a pyright release that closes it breaks the idiom, so v2
ships a pyright-version canary test asserting both halves, and documents basedpyright as the
supported checker (pydantic's mypy plugin already flags the pattern).

---

## 6. What replaces MISSING, precisely

| v1 idiom | v2 |
|---|---|
| `AllowMissing[T] = MISSING` + `__post_init__` fill-in (dynamically resolved default) | one line in `__derive__`; soft assignment replaces the `if x is MISSING` guard |
| `AllowMissing[T] = MISSING` as "user must provide before finalize" | a required field, simply unset on the draft; finalize fails per-leaf with site info |
| deep upward wiring (v1 had no story at all; the "massive if-else" at the root) | `C.inherit(Anchor).field` at the leaf, or a typed `C.descendants` broadcast at the provider (3.5) |
| `validate_no_missing()` | nothing; there is no sentinel to sweep for |
| MISSING surviving into dumps / schemas | impossible; nothing symbolic exists in a final |
| genuine tri-state "absent is data" (rare) | `T | None`, or pydantic's experimental sentinel under its own name someday; explicitly not v2's concept (and verified unpicklable today, which kills it for the cloudpickle workflow anyway) |

---

## 7. The honest-lie ledger

With live drafts, `Cls.draft() -> Self` stops being a lie at all: the draft IS an instance of
`Self` (`isinstance` passes, repr/eq/pickle are real). What remains is a short list of places
where the static picture and the lifecycle diverge; each is listed with its leak and its loud
runtime gate; none is silent.

| Gap | Where it leaks | Gate |
|---|---|---|
| Draft reads typed `T` can raise | reading a required-unset field | `UnsetError` with the full dotted path; did-you-mean on typos |
| Drafts and finals share a static type | a function annotated to take a "final" can receive an unvalidated draft and the checker cannot tell | `C.is_draft()` for boundaries that care; `C.finalize(x)` is idempotent so boundary code can normalize defensively; dump methods on drafts raise `DraftError` |
| Mutating a frozen-typed class typechecks | pyright does not enforce `ConfigDict(frozen=True)` (only the class-kwarg spelling); draft assignment relies on exactly this gap | runtime frozen check rejects writes to finals; pyright-release canary asserts both halves of the blind spot (section 5) |
| Soft assignment in `__derive__` may no-op | invisible to the checker (a semantic, not a type) | by design: the no-op IS the override feature; the scheduler's agreement check (3.3) catches the pathological orderings |
| `inherit` defaults make a field look defaulted | `LNConfig()` typechecks but cannot resolve outside a tree; and no checker can prove an enclosing anchor will exist | `validate_default` rejects the marker at direct construction with a pointed message; finalize errors name the leaf path and the searched anchor |

Everything else is plainly typed: hook bodies, helper functions, overrides, finals. There are no
proxy value types, so there is no class of "expression escaped into a final config" or
"expression `__eq__` returned False" bugs to gate against; the panel reproduced both in the
expression-based alternative.

---

## 8. Alternatives explored and why they lost

The panel scored seven designs under three adversarial lenses (type-safety, reliability,
simplicity). The judges split 2-1 between the top two; the synthesis above takes the simplicity
judge's mechanism with the reliability judge's scheduler repair and the type-safety judge's
engineering checklist. One panel verdict was later overturned: the lifecycle judges preferred a
shadow-tree draft over "live drafts" (real `model_construct` instances) partly on an overbroad
reading of the setattr-cache hazard; the source-level study in section 4 corrected that, and the
live-draft shape now wins on its merits (real isinstance, native provenance, less owned code).
Kill reasons for the rest, briefly and honestly:

### 8.1 Expression rules recorded by typed proxies (the runner-up, and it was close)

```python
with BoltzConfig.derive() as b:
    b.diffusion.dim = b.trunk.dim            # plain assignment records a rule
    b.diffusion.cond_dim = 2 * b.trunk.dim   # operators build expressions
```

A working 740-line prototype passed everything: declarative per-path rules with a clean
precedence ladder (static default < inner-class rule < outer-class rule < draft entry), per-path
overrides, `del` re-arming, field-level cycle chains ("diffusion.cond_dim -> trunk.dim ->
diffusion.cond_dim"), edit-time rename catching, and (uniquely) honest direct constructors by
compiling self-scoped rules into real pydantic data-aware default factories, so `TrunkConfig()`
alone gets `head_dim` filled. Two judges ranked it first.

Why it lost anyway: (1) expressions cover attribute paths and arithmetic only; conditionals,
f-strings, and method calls on references typecheck and then fail at runtime, and anything
fancier routes through `compute(fn, *refs)`, which is the lambda-ref the author already
declined wearing a trench coat. (2) Proxy value types need permanent dunder hygiene: the panel
reproduced `==` on a reference returning False silently and an f-string minting `"<ref ...>"`
that validated into a str field; every such dunder must be made to raise, forever. (3) The
honest-constructor compilation retrofits `FieldInfo` and forces dependent `model_rebuild`s, which
is pydantic-internals-adjacent machinery with a registry, a lock, and two evaluation engines for
the same rule. Each issue is fixable; together they are a maintenance bomb attached to a second
way of writing what `__derive__` writes in plain Python. If declaration-adjacent rules prove
genuinely valuable later, this exact surface can be added ADDITIVELY as a recorder that compiles
to a generated `__derive__`, reusing the v2 runtime unchanged. That is the sanctioned upgrade
path; it is not in v2.0.

### 8.2 Typed shared variable blocks (`class Dims(C.Vars): model_dim: int`)

Verified to typecheck beautifully (class-level access of an annotated attribute is typed `int` by
pyright with a hidden metaclass `__getattr__` supplying runtime refs). Lost because it cannot
express field-to-field references (trunk.dim and diffusion.dim can still quietly diverge if both
follow the knob but one is overridden), DI-style scoped providers introduce silent-shadowing
hazards, and the workflow it serves collapses into the zero-machinery pattern of section 3.4.
Engineering fact worth keeping from its verification: any metaclass subclassing pydantic's
`ModelMetaclass` silently kills pyright's synthesized `__init__` checking unless
`@dataclass_transform` is re-applied; that goes in the v2 checklist should any metaclass ever
appear.

### 8.3 Pydantic-native only (validators + data-aware factories + context)

The control group. Confirmed that data-aware `default_factory` is same-model, definition-order
constrained (verified: referencing a later field raises a raw `KeyError` through
`model_construct`), and construction-time (stale under draft mutation), so it cannot carry
cross-module derivation. Root `model_validator` derivation forces hand-written
override-vs-derive guards via `model_fields_set`, which is exactly the v1 `__post_init__`
boilerplate v2 exists to delete. Two permanent take-aways: `__pydantic_fields_set__` is the
native provenance primitive the draft layer should mirror, and the discovered landmine catalog
(below) becomes a regression suite.

### 8.4 Declaration-site relative references and protocol-typed mountings

`C.parent.dim`-style relative refs are untypeable from inside the child (the parent type is
unknowable; everything collapses to Any, which is invisible to renames). The one salvageable
piece: an opt-in static assertion `C.satisfies[HasTrunk](BoltzConfig)` (Generic-subscript form
only; the two-arg function form silently widens, verified; protocol members as read-only
properties to dodge mutable-attribute invariance, verified) that checks a mounting relationship
at edit time.

Post-panel revision: this verdict was half right. What is untypeable is *positional* upward
reference (parent-of-parent, hop counts); what section 3.5 ships instead is *type-anchored*
upward reference, where the child names the anchor CLASS and pyright checks the field access
against it. The panel's kill applies to `C.up(3).dim`; it does not apply to
`C.inherit(DimSource).dim`, which is why declaration-site upward interpolation came back once
the anchor idiom solved the typing and the import direction simultaneously.

### 8.5 Everything else

String DSLs (`"${trunk.dim}"`): zero type safety; the thing being replaced. Reactive/live
linking (values stay connected after finalize): action at a distance; a run record must be dead
data. Constraint solvers (declare `dim == dim`, let unification assign): wildly over-powered for
"copy a number"; failure modes are solver opacity. Reading a draft field minting a live
reference (`cfg.diffusion.dim = cfg.trunk.dim` recording a link): one attribute access cannot be
both a value and a reference; helpers must branch on real values (`if cfg.trunk.dim > 512`), and
the panel's lifecycle track established honest draft reads as a hard requirement.

---

## 9. Deleted from v1, and what replaces each

| v1 (5,204 source lines) | v2 |
|---|---|
| Registry (752 lines) | a docs page: `Annotated[A | B, Field(discriminator="kind")]` is native and verified, including error quality; dump-side polymorphism for base-annotated fields via `polymorphic_serialization` (2.13, verified) with the caveat that its `revalidate_instances` interplay needs dedicated tests before being a default |
| Codegen/export + json_schema + vendored typing_inspect (2,391 lines) | deleted, no replacement; `model_json_schema()` covers editor schemas, deterministic since 2.13 |
| YAML/TOML/JSON file helpers, `$schema` injection plumbing | `model_dump_json` / `model_validate_json`; a 5-line recipe in docs for `$schema`; at most a 2-line `from_json_file` kept for symmetry |
| `from_python_file` / `from_python_module` / `__create_config__` protocol | deleted; "Configs are Python" docs page (section 10) |
| MISSING / AllowMissing / validate_no_missing (109 lines + config hooks) | section 6 |
| MutableMapping facade, Lightning hparams bridge | `final.model_dump()` at the call site |
| Singleton (233 lines) | userland (a module-level variable) |
| `include_literals` serializer, hash patching, draft `model_config` patching, version shims | all structurally unnecessary: finals are frozen (native hash), there is one validation entry, drafts are not pydantic objects, floor is 2.13 |
| treescope adapter (87 lines) | kept as an optional ~100-line extra; `__rich_repr__`/`__pretty__` are native, so rich/devtools cost nothing |

---

## 10. Everything in Python: the workflow story

The panel's verdict on `from_python_file`: **keep-as-idiom, delete the API.** In an
everything-in-Python world a "config file" is a module, and `from myproj.experiments.boltz import
boltz_large` already provides identity, caching, pickling, and type-checking; a library loader is
a second, strictly worse import system, and `__create_config__` is a magic-name DSL of exactly
the species v2 deletes.

One verified result here overturns a REVIEW.md suggestion and is worth recording. The cloudpickle
behavior matrix (verified on cloudpickle 3.1.2):

1. Class in an importable package: pickled by reference, tiny payload, best path.
2. Class defined in a notebook (`__main__`): pickled by value (~4 KB), unpickles on a worker with
   only pydantic installed, validators intact. The flagship workflow works natively.
3. Class from a file loaded the v1 way (synthetic module, NOT in `sys.modules`): plain pickle
   fails, cloudpickle falls back to by-value and works. v1's "bug" is, for this workflow, the
   working configuration.
4. Same file with the module registered in `sys.modules` (REVIEW.md's proposed fix): cloudpickle
   now pickles by reference and the worker dies with `ModuleNotFoundError`. The natural fix
   breaks the flagship workflow.
5. Case 4 plus `cloudpickle.register_pickle_by_value(module)`: works, but requires the loader to
   know about cloudpickle. This is the only correct registered-module recipe, and it is why the
   loader does not belong in the library.

The run-record convention (a docs page, ~20 lines of user code, possibly a `nshconfig.runs`
micro-module later if demand materializes):

- `run_dir/config.json` = `final.model_dump_json(indent=2)`: THE record, concrete values only.
- `wandb.config.update(final.model_dump(mode="json"))`.
- `run_dir/provenance.json`: git SHA, dirty flag, lock hash, config class qualname, versions.
- The cloudpickle payload is transport and cache, never the record. The config `.py` is a
  program, not a record; re-executing it against a moved codebase is silent non-reproduction.
  Reproduction = `BoltzConfig.model_validate_json(record)` at the provenance SHA.
- Sharing an experiment = pushing a branch with a named helper (`def exp_42(cfg): ...`), or
  sending `config.json`. Diffs between experiments are `git diff`, not YAML diffs.

---

## 11. Engineering substrate (all items verified during the panel)

- **Floor: pydantic >= 2.13, Python >= 3.10.** Not negotiable downward: two agents independently
  reproduced a pydantic 2.12 bug where cloudpickled `__main__`-defined models with nested
  `mode="after"` model validators silently `model_dump()` to `{}` cross-process; 2.13 fixes it.
  Consequences regardless of floor: the `Config` base uses before-validators and
  `validate_default` only, and CI keeps a subprocess-cloudpickle-then-dump canary. File the bug
  upstream. Python 3.12 is not justified as a floor; nothing verified needs it.
- **Pydantic touchpoints**: `model_fields` (read), `model_construct` (draft creation),
  `__pydantic_fields_set__` (provenance), `model_validate` (the boundary), and
  `ConfigDict(frozen=True)`. All public or documented surface; the one inherited sharp edge is
  that `model_construct` lets a user data-aware `default_factory` raise a raw `KeyError` on
  field-ordering violations, which v2 documents as a loud failure at `draft()` time (section
  4.1) rather than wrapping.
- **CI canaries**: per-pydantic-release smoke (the two touchpoints); pyright-release canary for
  the ConfigDict-frozen blind spot (section 5) and proxy-assignment strictness; the
  cloudpickle subprocess round-trip; an 8-thread draft-isolation test and a free-threaded
  (3.13t/3.14) job, since all draft state is instance-local by design and the claim should be
  tested, not assumed.
- **Notebook caveat to document**: `use_attribute_docstrings` is source-inspection based and
  silently yields no descriptions for classes defined inside an IPython kernel (verified).
  Config classes belong in modules; notebook-defined classes work fully otherwise.
- **Sweeps and copies**: `model_copy`/`__replace__` skip validation (verified), hence
  `final.replace()` routes through thaw-finalize. pydantic 2.14 changes `model_copy` deep-copy
  behavior; the thin wrapper isolates v2 from it.
- **module layout** (revised after the live-draft redesign; core ~700-950 LOC vs v1's 5,204):

  ```
  src/nshconfig/
    __init__.py    (~40)   explicit public surface; ships py.typed
    config.py      (~150)  Config base: frozen finals, curated model_config, draft() classmethod,
                           the three attribute dunders (draft writes/vivification/del), dump gates
    derive.py      (~300)  hook collection, iterate-to-agreement scheduler, soft-assignment switch,
                           inherit markers + parent map + spine vivification + pull resolution,
                           descendants() iterator
    finalize.py    (~150)  collect (draft collapse, {}-emission), model_validate, BuildError
                           annotation, fields_set restoration, thaw, replace
    errors.py      (~100)  UnsetError, DeriveCycleError, InheritError, BuildError, DraftError;
                           dotted-path/site rendering
    treescope.py   (~100, optional extra)
  ```

  The dedicated draft module disappears: drafts are `Config` instances, and what used to be a
  shadow-tree object model is ~120 lines of dunder overrides living in `config.py`. The upward
  layer (3.5) prototyped in ~120 lines; core estimate lands around 850-1,100 LOC.

---

## 12. Open questions, ranked

1. **Scheduler proof.** Prototype the iterate-to-agreement loop (3.3) against adversarial hook
   sets: knob fan-out under both orders, mutual cycles, depth-3 chains, impure hooks, and the
   3.5 interactions (hooks reading inherited fields, pulls whose providers are hook-derived).
   Standalone pieces are verified (chained pulls needed and survived multiple rounds); the
   combined scheduler is the one piece still ahead of its prototype.
2. **Anchor policy extensions.** Nominal anchors (`isinstance`) shipped; decide whether
   structural anchors (`Protocol`-based, "nearest ancestor with a dim field") are wanted, with
   their wrong-level-capture risk, and whether `inherit` should support `strict=` nearest-vs-
   unique semantics for trees with repeated anchors.
3. **Verb naming.** `finalize` vs `build`; `thaw` vs `to_draft`. Cosmetic but permanent.
4. **Container derivation.** Hooks touching `list[SubConfig]` elements (iterate drafts? index
   assignment only?). Collect already recurses into containers; in-place element thawing sugar
   and the derive-pass semantics over containers need one decision and tests.
5. **Eager per-field validation on draft assignment.** pydantic's `validate_assignment` handler
   exists and could optionally check each draft write immediately; deferred to keep exactly one
   validation boundary, but the live-draft shape makes it cheap to add later as an opt-in.
   (`draft(**kwargs)` seeding, previously open, is answered: `model_construct` provides it
   natively and seeds land in `fields_set` as explicit provenance.)
6. **Lenient loading.** `model_validate(data, extra="ignore"/"allow")` is native; decide whether
   v2 ships intent-revealing sugar that reports dropped keys (config archaeology) or leaves it as
   a recipe. Leaning recipe-first, sugar in v2.1 if it recurs.
7. **`nshconfig.runs`.** The record/provenance convention as a micro-module vs docs-only.
   Docs-only first.
8. **Treescope extra.** Keep if the notebook rendering is still loved; rich repr is free either
   way.
