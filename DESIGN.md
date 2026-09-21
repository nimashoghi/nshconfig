# nshconfig v3 contract

nshconfig replaces configuration composition with typed Python. Config files build drafts, ordinary Python functions apply presets, and an entry point passes a finalized config to application code. Interpolation is the only deferred computation abstraction. There is no launcher, registry, configuration language, or provenance system.

## Lifecycle and typing

An annotated `Config` subclass defines a schema. Bare annotations declare required fields. Defaults use ordinary Python values, `Field(default_factory=...)`, or `interp(...)`. Constructors and `.draft()` both create editable drafts. Normal keyword constructors have dataclass-transform signatures, so type checkers require fields without defaults. `.draft()` takes no arguments and permits incomplete construction. Runtime writes defer value validation, including constructor keyword values. Unknown fields fail immediately.

Draft and final objects share the schema type. Type checkers check field names, values, callback results, and constructor arguments; they do not prove completeness or distinguish mutability. Runtime attribute interception is hidden from static analysis so it does not open the schema to arbitrary attributes. The test suite checks positive and negative fixtures with ty, Pyright, and mypy without plugins or generated stubs.

Reading a field resolves and validates that field and its dependencies. Reading an ordinary child config checks its node type without requiring the child or root to be complete. Missing fields raise `MissingValueError`. Deleting a draft field unsets it, including fields with defaults; deletion does not restore a default.

`.finalize()` resolves the whole subtree, creates an independent snapshot, and runs cross-field checks. The draft remains editable with its interpolation rules. Config nodes and managed containers in the snapshot reject mutations. A subsequent draft edit or finalization cannot change a prior snapshot. `.copy()` makes an unattached editable copy: copying a draft preserves rules; copying a final preserves its already resolved values.

## Interpolation and validation

`interp(lambda c: c.root(Run).width)` defines a whole-field computation. `Context.root(T)` selects the root and verifies its type. `nearest(T)` selects the closest enclosing Config of type T, excluding the current node and ignoring intervening containers. `current(T)` selects the current Config. Missing context and dependency cycles raise `InterpolationError`; cycle errors include the field chain. Declaration order does not constrain dependencies.

Each top-level read or finalization owns a resolution session. Interpolation results are memoized within that session, never across edits or independent reads. Explicit values retain a separate canonical cache until the field or a contained value changes. Canonical normalization does not overwrite raw inputs, so a non-idempotent normalizer does not run repeatedly over its own output. Reads of dependencies see canonical values.

Callbacks and field normalizers must be pure. They may read their config tree but cannot mutate it while it is resolving. External I/O, environment reads, randomness, and side effects should happen in ordinary builder code before finalization. Configs are single-writer objects; simultaneous mutation and resolution from different threads is unsupported. Independent trees and completed snapshots can be read concurrently.

Scalar reads are ordinary Python snapshots. Assigning a scalar replaces a rule; `cfg.width += 1` evaluates the rule and pins the result. Interpolated Configs and containers are completed, read-only computed values. Replace the whole field with an editable draft to customize it. Existing Config nodes returned by a callback resolve in their source context and are copied. Newly constructed nodes resolve in the destination context. Results never take ownership of source nodes.

Pydantic TypeAdapter provides strict field validation, `Annotated` constraints, `BeforeValidator`, `AfterValidator`, `Field`, and `StringConstraints`. nshconfig is not a Pydantic BaseModel and does not expose its unrestricted model mutation/serialization API. Field normalizers are field-local; cross-field derivations use interpolation. Container normalization can normalize leaves but must preserve shape, mapping keys, and Config node identities. Compute a differently shaped container with interpolation or builder code.

`@check` marks an instance method returning `None`. Finalization runs these methods after all values are resolved, on the frozen snapshot. A check may raise an error but cannot rewrite the graph or return a replacement. Inherited checks run unless overridden; overriding a check requires decorating the replacement if it should still be a check. Ordinary field reads do not run whole-model checks. Reading a computed subtree checks that completed subtree.

## Ownership and containers

Each explicit Config child has one owner. Assignment preserves its identity, and edits through an existing reference affect the attached child. Reuse requires `.copy()`. Replacing or removing a child detaches it. Class-declared defaults are recursively copied for each new parent, including container defaults. Ownership changes are checked before committing a mutation; duplicates, structural cycles, and competing owners fail without partially attaching incoming children.

Incoming plain lists and dictionaries are copied recursively into managed mutable sequences and mappings. Config elements retain their identity and ownership requirements. Aliases to the original plain containers do not affect the config. Aliases obtained by reading config fields remain live, including nested container aliases. List indexing, slicing, append/extend, removal, reverse/sort, and dictionary mutation use the managed boundary. Augmented assignment retains the managed field identity. Duplicate Config elements are rejected, including list multiplication that would duplicate ownership.

The public annotations remain `list[T]` and `dict[K, V]` for familiar static typing. Runtime values implement `MutableSequence` and `MutableMapping`; they are not built-in list/dict instances. Use `.to_dict()` on a final or `list(...)`/`dict(...)` when an external API requires concrete built-ins. Private attributes and explicitly bypassing Python attribute hooks are outside the read-only contract.

Tuples may contain managed containers and Config nodes. Frozen sets may contain supported immutable leaves. Mutable sets and arbitrary mutable opaque objects are outside the supported value graph and fail at finalization. Supported immutable leaves include scalar primitives, enums with immutable values, paths, dates/times/durations, Decimal, UUID, ranges, and types. There is no claim to freeze arbitrary user-defined objects. Represent mutable configuration state with Config, list, dict, or tuple.

## Export, annotations, and transport

A final supports `.to_dict()` for detached ordinary Python containers and `.to_json()` for JSON with Pydantic scalar encodings. Draft export requires explicit finalization. There is no implicit dict-to-schema conversion: construct typed child configs in Python.

Eager annotations, quoted references, postponed annotations, and Python 3.14 annotations are supported. Module-level forward types resolve when fields are first read. Use `Schema.rebuild(namespace={"Child": Child})` for late function-local references. Concrete resolved annotations are retained on the class; compiled Pydantic adapters are process-local and excluded from class transport state.

Standard pickle supports importable schemas. The optional `transport` extra supplies cloudpickle for notebook-defined schemas and interpolation callbacks. These are trusted executable Python payloads for short-lived transport, not stable archival formats. Import does not install global pickle reducers. Subprocess transport tests run across the supported Python/Pydantic matrix.

## Implementation boundaries

`_src/schema.py` handles declarations, annotation resolution, defaults, and compiled field adapters. `_src/runtime.py` owns the draft tree, managed containers, resolution sessions, ownership transactions, snapshots, and checks. The public surface is explicitly exported in `nshconfig/__init__.py`.

v3 intentionally removes the v2 template/final-constructor lifecycle, declaration-order interpolation, `config_draft`/`config_finalize`, unrestricted Pydantic authoring re-exports, shallow finals, and treescope integration. No v2 compatibility layer is maintained.
