# Interpolation

`C.interp(lambda c: ...)` returns a **value** (an `Interp` marker) that resolves against the
config tree during validation. Because it is just a value, one concept covers everything
Hydra/OmegaConf split across class-level defaults and composition-time interpolation:

```python
class LNConfig(C.Config):
    dim: int = C.interp(lambda c: c.nearest(ModelConfig).dim)   # class default slot

cfg.model.encoder.ln.dim = C.interp(lambda c: ...)              # draft assignment
TrainConfig.model_validate({"model": {"dim": C.interp(lambda c: c.root.width)}})  # dict input
a: int = pydantic.Field(default=C.interp(lambda c: ...), gt=0)  # inside Field metadata
```

Nothing special happens at class definition: pydantic stores the marker verbatim in the
default slot, exactly like `= 32`. The single resolution rule, per field in declaration order:
`value.get(name, field.default)`; if that is a marker, call it with a `Ctx`. An instance
marker is found *at* the key; a class-level marker is the same kind of object found in the
default slot when the key is absent.

## The Ctx API

| Accessor | Hydra equivalent | Sees |
|---|---|---|
| `c.data` | same level | own fields; earlier-declared markers already resolved |
| `c.parent` | `${..x}` | one level up; ancestor frames are always resolved |
| `c.root` | `${a.b}` | the validation root; raw input + class defaults, incl. sibling subtrees |
| `c.nearest(Cls)` | (no equivalent) | nearest enclosing `Cls` instance, ancestors only |

Prefer `c.nearest(SomeConfig)` over positional forms: it anchors on *meaning*, so it survives
restructuring (nest a subtree one level deeper and nothing breaks) and disambiguates repeated
classes (in a teacher/student tree, each LN binds to *its* model). Views support attribute
access only; everything they return is plain Python, so the lambda body is ordinary pure
Python: arithmetic, conditionals, `min`/`max`, f-strings over resolved values.

## Precedence: one rule

Explicit always beats interpolation, mechanically (input-slot presence beats the default
slot). Spelled out:

| rung | source |
|---|---|
| 1 | concrete user value (write, kwarg, dict) |
| 2 | marker as user value (instance slot) |
| 3 | marker as class default |
| 4 | static default / default_factory |
| 5 | required and absent: missing error |

Last write wins among user writes; `del` re-arms the rung below; markers may satisfy required
fields; resolved values still pass field constraints (`gt=0` and friends), so interpolation
feeds validation rather than bypassing it.

## Ordering and the one-pass rule

Each scope resolves *before* descending, so ancestor reads (`c.parent`, `c.nearest`) always
see resolved values, and same-level chains work in declaration order. Dotted descent from
`c.root` sees raw input plus static class defaults: a sibling value that is *itself* still
pending fails loudly with both ends named, rather than resolving lazily. Point both fields at
the shared source instead:

```python
cfg.a.x = C.interp(lambda c: c.root.width)   # not: c.root.b.y where b.y is also pending
cfg.b.y = C.interp(lambda c: c.root.width)
```

One pass, no lazy re-entry: a finalized value can never depend on evaluation order invisibly,
and cycles are impossible to construct silently.

## Marker hygiene

Pending markers refuse to be data: `bool(marker)` and f-strings raise, arithmetic raises, and
a root-level sweep rejects any marker that survives validation outside a declared field slot
(for example, smuggled inside a list under an `Any` field) with its exact path. The one
documented sharp edge: `==` on markers is identity, so two distinct markers compare unequal
rather than raising.
