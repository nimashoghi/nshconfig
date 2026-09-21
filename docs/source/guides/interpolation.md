# Interpolation and validation

`C.interp(lambda c: c.root(Run).width)` defines a live whole-field rule. `root(T)` verifies the root type; `nearest(T)` selects the closest enclosing Config of type T, excluding self; `current(T)` selects the current Config. Containers do not count as enclosing Configs.

Reads resolve dependencies on demand, independent of declaration order. Missing fields and dependency cycles fail explicitly. Values are canonical: field-local normalization runs before another interpolation sees a dependency. Interpolation memoization lasts for one top-level read or finalization.

Assignments replace rules. Augmented scalar assignment reads the current value and pins the result. Ordinary scalar reads and Python control flow have snapshot semantics. Interpolated subtrees are read-only completed values; replace the whole field with a draft to customize it.

Use `Annotated` with `C.Field`, `C.BeforeValidator`, or `C.AfterValidator` for field-local validation and normalization. Raw inputs are preserved separately from normalized values. Container normalizers must preserve shape, keys, and Config identities; use interpolation or builder code to compute a different container.

Use `@C.check` instance methods returning `None` for cross-field checks. They run on the completed frozen snapshot and may raise, but cannot rewrite values. Keep normalization and interpolation pure. Collect environment variables, file contents, and other external inputs in ordinary builder code.
