---
name: nshconfig
description: Author typed Python config drafts with nshconfig v3, contextual interpolation, and read-only finalized snapshots.
---

Use ordinary annotated `nshconfig.Config` subclasses. Bare annotations declare required fields. Create incomplete state with `Schema.draft()` and short configs with checked keyword constructors. Both produce editable drafts. Apply presets using ordinary Python functions and pass `config.finalize()` to application code.

Define live whole-field computations with `C.interp(lambda c: c.root(Run).width)`. `c.nearest(Model)` selects an enclosing schema and `c.current(Child)` selects the current node. Dependencies may appear in any declaration order. Reads return validated current values; assignment replaces the rule. Computed subtrees are read-only: replace their whole field with a draft to customize them.

Use `Annotated` with `C.Field`, `C.BeforeValidator`, or `C.AfterValidator` for field-local validation. Use `@C.check` methods returning `None` for final cross-field checks. Checks run on a read-only snapshot and may raise; derive values with interpolation instead of rewriting fields in a check.

Explicit child assignment preserves identity and permits one owner. Use `.copy()` to reuse a child. Defaults are automatically copied per parent. Incoming lists/dicts are imported into managed sequence/mapping containers; config-returned aliases stay live. Use a final's `.to_dict()` when concrete built-in containers are required by another API.

Finalization leaves the draft and its rules intact. Final config fields and nested containers reject mutation at runtime. Static checkers check field types, not completeness or draft/final mutability. Arbitrary mutable opaque leaves and mutable sets are unsupported. Keep callbacks pure, and collect external inputs in builder code.

For late function-local forward annotations, call `Schema.rebuild(namespace={"Child": Child})`. Use optional cloudpickle transport only for trusted short-lived Python payloads. See `DESIGN.md` for the exact lifecycle and container normalization limits, and `examples/af3.py` for composition and execution without a launcher framework.
