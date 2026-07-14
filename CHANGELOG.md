# Changelog

## 2.1.0a0

This alpha replaces the legacy mutable configuration API with an explicit lifecycle
for typed ML-run configuration:

- `draft()` creates reusable composition recipes without running validators or
  defaults; `finalize()` produces ordinary validated, field-frozen `Config` values.
- `interp()` derives complete fields in Pydantic declaration order through typed,
  read-only, resolver-local `Context` views.
- provenance records assignments, mutations, defaults, interpolation reads, and
  source labels; `explain()` follows those dependencies.
- deterministic fingerprints bind concrete type identity, alias-aware schema
  identity, exact runtime semantics, and canonical JSON values.
- inert run-record v4 envelopes store seven fields, including separate schema,
  semantic, and value fingerprints, and verify provenance without executing
  interpolation.

The redesign intentionally does not preserve compatibility with the 0.x API or with
run-record formats before v4. It requires Python 3.10 through 3.14 and Pydantic 2.13
or newer. See `DESIGN.md` for the complete semantic contract.
