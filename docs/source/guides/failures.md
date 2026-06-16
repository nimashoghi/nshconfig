# Failure model

Every degradation is loud and names its location: the dotted instance path, the owning
`Cls.field`, and (for interpolation) the lambda's `file:line` site captured at construction.
There is no silent third outcome: `finalize` returns values consistent with every rule, or it
raises.

| Failure | When | The message carries |
|---|---|---|
| Orphan: `c.nearest(Cls)` finds no enclosing `Cls`; `c.parent()` at a root; `c.up(n)` climbs past the root | finalize / any validation | `cannot interpolate ln.dim [LNConfig.dim = interp(<fn @ file:line>)]: no enclosing ModelConfig (ancestors here: EncoderConfig > LNConfig)` |
| Typed selector mismatch, e.g. `c.root(TrainConfig)` on a different root | finalize / any validation | the expected class, actual frame class, and ancestor chain |
| Cycle: mutual markers across subtrees; self-reference | finalize | one `ValidationError` with *both ends* as entries, each naming the other's pending marker |
| Reading a still-pending sibling via `c.root()` | finalize | "is itself pending interpolation (...) (possible cycle; set a concrete value, or point both at the same concrete source)" |
| Marker outside a declared field slot (e.g. inside a list under `Any`) | root sweep after validation | the exact path: `leaked into the final at Meta.meta[0]` |
| Reading a pending/unset field on a draft | compose time | `UnsetError` with the path; "read it after finalize()" for interpolated fields |
| Using a pending marker as data (`bool`, f-string, arithmetic) | compose time | `DraftError`/`TypeError` naming the marker and site |
| Dumping a draft | compose time | `DraftError: drafts are not serializable; finalize() first` |
| Resolver raised (any exception in the lambda) | finalize | wrapped with the dotted path and site |
| Resolved value violates field constraints | finalize | pydantic's ordinary constraint error on the derived field |
| Required field nobody set | finalize | pydantic's per-leaf missing error (`model.encoder.ln.dim`, not `model`) |
| Typo'd draft write | compose time | `AttributeError` with a did-you-mean; also a static basedpyright error |

Two failure modes are *impossible by construction* rather than detected: infinite resolution
loops (there is no recursive resolution; pending reads raise immediately, and that is the
cycle detector) and symbolic values inside finalized dumps (the root sweep guarantees it).
