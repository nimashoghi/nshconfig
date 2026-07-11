# API reference

The public surface is the names in `nshconfig.__all__`. Pydantic authoring APIs are
not re-exported; import them directly from `pydantic`.

## Schema and lifecycle

```python
class Config(pydantic.BaseModel):
    record_schema_id: ClassVar[str | None] = None

Config() -> None
def draft(cls: type[C]) -> C
def finalize(config: C) -> C
def is_draft(obj: Any) -> bool
```

`Config` is the base class for schemas and field-frozen finals. Calling a subclass
uses its Pydantic-generated field signature, runs validation, and returns a final;
the fieldless base class itself has the signature shown. `draft()` creates mutable,
possibly incomplete composition state without seed values. `finalize()` resolves and
validates a draft; on a final it performs value revalidation into a fresh final.
`is_draft()` is a narrow predicate: it returns `True` only for an established draft,
and returns `False` for finals, in-progress instances, and unrelated objects.

`record_schema_id` participates in record identity. Leave it as `None` for a
uniquely named concrete class whose `module:qualname` is stable. An exact generated
generic parameterization must declare a distinct non-empty value. A concrete class
may also declare one to version behavior that the schema fingerprint cannot express.

`Config` retains Pydantic's normal validation and serialization methods, subject to
the lifecycle contract. `model_construct()`, `copy()`, `model_copy(update=...)`, and
direct instance reinitialization are rejected. Serialization, iteration, and copying
that require a final reject drafts and in-progress validation instances.

## Interpolation

```python
def interp(fn: Callable[[Context], T]) -> T

class Context:
    def current(self) -> Any
    def current(self, cls: type[M]) -> M

    def parent(self) -> Any
    def parent(self, levels_or_cls: type[M]) -> M
    def parent(self, levels_or_cls: int) -> Any
    def parent(self, levels_or_cls: int, cls: type[M]) -> M

    def root(self) -> Any
    def root(self, cls: type[M]) -> M

    def nearest(self, cls: type[M]) -> M
```

`interp()` is typed as the callable's result even though it returns an internal
marker until validation resolves the field. `Context` objects are created only while
an interpolation callable is running; calling `Context()` raises `TypeError`. A
context and every view derived from it expire when the callable returns and cannot be
used from another thread.
Selector class arguments must be `Config` subclasses. `parent()` uses one level by
default; `parent(levels, ConfigType)` combines an exact hop count with a runtime type
check.

## Provenance

```python
@contextmanager
def source(label: str) -> Iterator[None]

def explain(
    cfg: Config,
    path: str | Sequence[str | int],
) -> Explanation

def provenance(cfg: Config) -> dict[str, tuple[Event, ...]]
```

`source()` labels draft operations in its context. `explain()` accepts a canonical
path string or path-part sequence. `provenance()` accepts a draft or final and
returns a detached mapping with immutable event tuples.

`Event` is an immutable dataclass:

```python
Event(
    kind: Literal["set", "delete", "mutate", "interpolate"],
    value: str | None = None,
    file: str | None = None,
    line: int | None = None,
    function: str | None = None,
    code: str | None = None,
    label: str | None = None,
    operation: str | None = None,
    site: str | None = None,
    reads: tuple[tuple[str, str], ...] = (),
    value_token: str | None = None,
    read_tokens: tuple[str | None, ...] = (),
    from_default: bool = False,
)

def Event.describe(self) -> str
```

`value` and the values in `reads` are bounded display strings. `file`, `line`,
`function`, and `code` describe a draft operation when source capture succeeds.
`operation` belongs to `mutate`; `site`, `reads`, integrity tokens, and
`from_default` belong to `interpolate`. Record loading enforces the field combination
for each event kind.

`Explanation` is an immutable dataclass:

```python
Explanation(
    path: str,
    current: str,
    events: tuple[Event, ...] = (),
    origin: str | None = None,
    causes: tuple[Explanation, ...] = (),
)
```

`str(explanation)` and `repr(explanation)` render the explanation tree.

## Fingerprints and run records

In the signatures below, `JsonValue` means `None`, `bool`, `int`, finite `float`,
`str`, a list of JSON values, or a string-keyed dictionary of JSON values. It is
notation for the closed record format, not an additional exported symbol.

```python
def fingerprint(config: Config) -> str
def record(config: Config) -> RunRecord
def load_record(
    cls: type[C],
    value: RunRecord | Mapping[str, Any],
) -> C
```

All three functions require a `Config` class or instance as shown. `fingerprint()`
and `record()` require a completed final. `load_record()` validates concrete values
without running interpolation.

`RunRecord` is an immutable, slotted dataclass with a keyword-only constructor:

```python
RunRecord(
    *,
    format: str,
    config_type: str,
    schema_fingerprint: str,
    semantic_fingerprint: str,
    fingerprint: str,
    values: Mapping[str, Any],
    provenance: Mapping[str, Any],
)

RunRecord.from_dict(value: Mapping[str, Any]) -> RunRecord

record.values: dict[str, JsonValue]
record.provenance: dict[str, JsonValue]

record.to_dict() -> dict[str, JsonValue]
record.to_json(*, indent: int | None = None) -> str
```

Its five stored public attributes are `format`, `config_type`,
`schema_fingerprint`, `semantic_fingerprint`, and `fingerprint`. The `values` and
`provenance` properties return detached JSON objects. `to_dict()` also returns a
detached seven-field envelope. `to_json()` preserves semantic object order and uses
deterministic compact JSON unless `indent` is provided.

## Exceptions

```python
class UnsetError(AttributeError): ...
class DraftError(TypeError): ...
class FingerprintError(ValueError): ...
class RecordError(ValueError): ...
```

- `UnsetError` reports a draft read with no concrete value.
- `DraftError` reports a lifecycle state that is too early for the requested
  operation.
- `FingerprintError` reports a final that cannot produce deterministic, exact record
  data.
- `RecordError` reports a malformed or non-matching run record.

Pydantic `ValidationError` remains the validation-boundary error for field,
interpolation, and lifecycle-integrity failures raised through a compiled schema.

## Exported symbols

```text
Config, Context, DraftError, Event, Explanation, FingerprintError, RecordError,
RunRecord, UnsetError, __version__, draft, explain, finalize, fingerprint, interp,
is_draft, load_record, provenance, record, source
```

`__version__` is a string from installed package metadata, or `"unknown"` when that
metadata is unavailable.
