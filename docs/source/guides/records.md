# Fingerprints and run records

A final is a live Python object. `C.record(final)` converts it into inert,
JSON-safe data that captures exactly what a run used.

The [semantic contract](../contract.md) defines the fingerprint inputs and the
versioned record envelope.

`final.model_dump_json()` remains ordinary Pydantic serialization. It contains
field values only and is not a run record because it omits the type and schema
identity, verified fingerprint, and provenance.

## Deterministic fingerprints

```python
digest = C.fingerprint(run)
assert digest.startswith("sha256:")
```

The value fingerprint includes the run-record format, exact config identity, schema
fingerprint, exact-runtime semantic fingerprint, and deterministic JSON-mode field
values. The semantic fingerprint is a versioned structural token for the complete
validated runtime graph. It distinguishes runtime state that canonical JSON alone
may not distinguish. Provenance is excluded, so the same concrete runtime graph
with different assignment histories has the same fingerprint. Different config
types remain distinct even when their fields have the same shape and values.

Dictionary insertion order is semantic and is preserved in record JSON. Reordering
a dictionary changes the fingerprint. Sets and frozensets have no insertion-order
contract, so their JSON arrays are sorted deterministically. Do not pass a record
through a tool that sorts JSON object members: it changes an order-sensitive record
and verification will fail.

Fingerprinting never falls back to `repr()`. Before returning a digest, it validates
the canonical JSON values back into the same concrete config type, requires every
field to be consumed, and checks exact reconstructed runtime semantics plus a second
JSON serialization. It rejects drafts, cycles, non-finite floats, non-string JSON
object keys, excluded fields, lossy or unstable serializers, and values that cannot
round-trip deterministically.

The digest detects changes; it is not a signature and does not authenticate the
record's author or origin.

## Schema identity

`schema_fingerprint` hashes presentation-stripped canonical and alias variants of
both Pydantic validation and serialization JSON Schema. Its type manifest includes
the aliases of every reachable nested Pydantic model and the record identity of
every reachable nested `Config`, including nested `record_schema_id` values. This
catches alias, nested-type, and JSON-schema contract changes even when the current
field values happen to match.

JSON Schema does not describe arbitrary Python validator, serializer, or
interpolation implementation. The schema fingerprint is therefore a contract guard,
not a hash of executable behavior. The normal config identity is
`module:qualname`. Generated Pydantic generic parameterizations may have colliding
names, so the exact generated class must declare a distinct stable
`record_schema_id`. Prefer a uniquely named concrete subclass, whose
`module:qualname` is already stable:

```python
from typing import Generic, TypeVar

import nshconfig as C


T = TypeVar("T")


class Box(C.Config, Generic[T]):
    value: T


class IntBox(Box[int]):
    pass
```

`IntBox` does not require an ID. It may still declare
`record_schema_id = "my-project.int-box.v2"` to version behavior that the schema
fingerprint cannot see. The ID is appended to `module:qualname`. Use a distinct ID
for every exact generated specialization, and bump an optional ID whenever an
incompatible custom validator or serializer change is not visible in the schema
fingerprint.

## Save a record

```python
from pathlib import Path

import nshconfig as C


run_record = C.record(run)
Path("run-config.json").write_text(
    run_record.to_json(indent=2),
    encoding="utf-8",
)
```

The v4 envelope has exactly seven fields:

```json
{
  "format": "nshconfig.run-record.v4",
  "config_type": "my_project.configs:Run",
  "schema_fingerprint": "sha256:...",
  "semantic_fingerprint": "sha256:...",
  "fingerprint": "sha256:...",
  "values": {},
  "provenance": {}
}
```

`RunRecord` is immutable. Its `values`, `provenance`, and `to_dict()` results are
detached JSON objects, so callers may mutate those copies without changing the
record. Because finals are only field-frozen, later mutation of a nested container
changes the live final's next fingerprint but not an existing `RunRecord`.

Fingerprinting and recording are not atomic with concurrent mutation. The library
compares structural snapshots around serialization, serializes twice, and checks the
final again after provenance capture; if it observes a change, it raises
`FingerprintError`. Synchronize access to the final. Detection is a fail-loud guard,
not a substitute for a lock.

## Provenance integrity

Rendered provenance values are bounded display text. A v4 interpolation event also
contains SHA-256 tokens made by a versioned structural encoding of the result and
every value it read. Loading verifies those tokens against the restored graph and
regenerates the display text instead of trusting it.

Interpolation provenance must be self-contained under the recorded `Config` root.
Its interpolation event must also be the final event for that field. Recording
rejects an out-of-root read, a later event that makes an interpolation claim stale,
an impossible self/later/incomplete-branch dependency, or an opaque result or
dependency without a stable structural token. These tokens
make provenance internally checkable, but they are unkeyed hashes and do not
authenticate an adversarially rewritten record.

## Load a record

```python
import json
from pathlib import Path

import nshconfig as C


wire_value = json.loads(Path("run-config.json").read_text(encoding="utf-8"))
restored = C.load_record(Run, wire_value)
```

`load_record()` verifies the v4 format, exact config identity, schema fingerprint,
semantic fingerprint, value fingerprint, complete field set, deterministic round
trip, and provenance references. The restored runtime graph must reproduce the
recorded semantic fingerprint exactly. It then restores the verified provenance. It
does not execute interpolation.
The interpolation prohibition is an internal capability, not a mutable
`ValidationInfo.context` flag, so model validators cannot disable it.

The requested config class's normal Pydantic validators still run. Every declared
field is present, so an interpolation default is never needed; if validation drops,
changes, or fails to consume a stored value, loading fails. Field removal is detected
before a default or default factory runs. Because every field is
explicit record input, the loaded model reports every field in Pydantic's
`model_fields_set`; the original constructor's unset/default distinction is
composition metadata rather than run-record data.

A record describes what ran, not how it was composed. It cannot recover a draft,
an instance interpolation callable, or the original sequence of Python mutations.
Keep source code and environment metadata separately when they are needed to
reproduce the composition program itself.
