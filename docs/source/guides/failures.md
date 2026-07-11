# Failure model

`nshconfig` rejects incomplete, ambiguous, or non-reproducible results instead of
emitting partial output. Preserve these errors at application boundaries; their
locations explain which lifecycle rule failed.

The [semantic contract](../contract.md) is the authority for the invariants behind
these errors.

| Error | Typical cause |
|---|---|
| `UnsetError` | Reading an unassigned required draft field or pending interpolation |
| `DraftError` | Using a draft or in-progress Config where a completed final is required |
| `AttributeError` | Assigning or deleting an undeclared draft field |
| Pydantic `ValidationError` | Missing final input, a failed constraint or validator, or interpolation failure |
| `FingerprintError` | Non-finite, cyclic, unstable, excluded, concurrently changed, or non-JSON-safe final data |
| `RecordError` | A malformed, tampered, wrong-type, wrong-schema, unverifiable-provenance, or non-round-tripping run record |
| `TypeError` or `ValueError` | An invalid API argument, class definition, or structural graph |

Nested Pydantic serialization may wrap a draft rejection in
`PydanticSerializationError`, as Pydantic does for serializer failures.

## Interpolation failures

Interpolation errors use the dependent field's Pydantic location. They identify the
unavailable source path and the resolver source site when available. Common causes
are:

- reading a field declared after the dependent field;
- descending through an ancestor branch that is still being validated;
- using `parent()` at the root or asking `nearest()` for a missing ancestor;
- returning an active partial branch instead of a completed value;
- raising inside the resolver;
- returning a value that fails the target field's constraints.

Reorder the source before its dependent field or change the selector to an already
validated ancestor. Do not catch an interpolation error and substitute raw input;
that would mix unvalidated and canonical values.

## Graph failures

Finalization also checks the structural value graph. A cycle, an interpolation
marker nested inside a container, or a config draft hidden under an opaque
annotation fails with its graph path. Declare nested config positions explicitly
with concrete config or container annotations.

Only exact built-in `dict`, `list`, `tuple`, `set`, and `frozenset` values form the
recursive container graph after model-before validation. A model-before validator
may normalize legacy or custom collection input into those built-ins. Any custom
collection or lazy sync or async input that remains afterward fails, including
iterators, generators, awaitables, and coroutines. Materialize and await them before
validation.

The recursive guarantee covers Pydantic declared, extra, and private state,
dataclass stored state, built-in containers, inspectable Python `__dict__` and
`__slots__` state, and known carriers such as functions, bound methods, partials,
weak references, and weak methods. Truly opaque extension state cannot be proven
safe and must not conceal lifecycle state. Opaque callables with uninspectable
captured state are rejected. Internal containers inside an atomic value do not
inherit Config's structural-container policy. A mapping value containing `Config`
structure also requires every key on its path to have exact type `str` or `int`.

Fingerprinting and records add stricter deterministic-JSON rules. They reject
non-finite floats, non-string JSON mapping keys, excluded fields, lossy or unstable
serializers, and values Pydantic cannot reconstruct exactly. There is no `repr()`
fallback.

Config class creation also rejects validator forms that can bypass or repeat the
field lifecycle: model `wrap`, field `plain`/`wrap`, and deprecated
`validator`/`root_validator` decorators. Use `before` or `after` validators.
Declared fields also cannot shadow lifecycle APIs such as `model_dump`,
`model_validate`, `model_copy`, or `model_fields_set`.
Model-before validators may normalize input, but they cannot discard explicit
declared fields. Field validators cannot use `PydanticUseDefault` to replace
explicit input.

Run records require the exact seven-field v4 envelope, matching schema, semantic,
and value fingerprints, and self-contained interpolation provenance with structural
integrity tokens. Interpolation must be the final event for its field. Exact
generated generic parameterizations need distinct `record_schema_id` values; a
uniquely named concrete subclass already has stable `module:qualname` identity.
Stored self, later-field, and incomplete-branch reads are rejected as impossible
under declaration-order publication.

Do not fingerprint or record while another thread mutates a shallowly frozen final.
Repeated serialization and structural snapshots detect observed changes and raise
`FingerprintError`, but the operation is not a concurrency boundary; synchronize
the caller.
