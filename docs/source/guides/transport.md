# Transport, security, and environments

Use cloudpickle when a live Python composition recipe must move from a notebook to
another trusted process.

The [semantic contract](../contract.md) defines the transport boundary and
annotation rule.

Install the transport extra in both environments:

```bash
pip install 'nshconfig[transport]'
```

```python
import cloudpickle

import nshconfig as C


work = C.draft(Run)
work.model.dim = 2048
work.model.norm.dim = C.interp(
    lambda context: context.parent(Model).dim * 2
)

payload = cloudpickle.dumps(work)
received = cloudpickle.loads(payload)
assert C.is_draft(received)

run = C.finalize(received)
```

The draft keeps its interpolation callables and provenance. Notebook and local
classes may be transported by value. Importable classes use normal pickle
by-reference behavior; importing `nshconfig` does not install global reducers for
Pydantic or pydantic-core objects.

## Trust and compatibility

Pickles are executable. Load them only from a trusted source and use them as
short-lived transport, never as the sole archive of a run.

This applies to both standard pickle and cloudpickle. A digest or encrypted channel
does not establish who created a payload; authenticate its origin and restrict who
can write to the transport channel.

Sender and receiver need the same Python version and compatible installed
dependencies. This includes packages referenced by annotations, validators,
serializers, and interpolation callables. Pin the environment used by both sides.

For classes that need by-value transport, do not enable PEP 563 with
`from __future__ import annotations`. Use normal eager annotations and quote a
forward reference only where the referenced name is defined later. This matches
notebook execution and keeps Pydantic's compiled schemas transportable on supported
Python versions.

## Transport is not a run record

| Need | Use | Contains executable Python? |
|---|---|---|
| Move a live draft or notebook-defined class between trusted processes | cloudpickle | Yes |
| Save what a completed run used | `C.record(final)` | No |

A v4 run record contains canonical JSON values, provenance, a concrete config type,
a schema fingerprint, an exact-runtime semantic fingerprint, and a value fingerprint
that binds them together. It does not contain interpolation callables and cannot
recover a draft. See [run records](records.md).
