# nshconfig

Typed Python configuration for ML runs, built on
[Pydantic](https://docs.pydantic.dev/).

`nshconfig` adds a small composition lifecycle to ordinary Pydantic models:

- build an incomplete, mutable draft with normal Python assignment;
- derive fields with Python callables that read validated config values;
- finalize once into a field-frozen, fully validated model;
- inspect why each value was chosen;
- save a verified, JSON-safe record of the concrete run.

There is no YAML language, registry, loader, code generator, or Pydantic re-export
layer.

**[Documentation](https://nima.sh/nshconfig/)** |
**[Semantic contract](https://github.com/nimashoghi/nshconfig/blob/main/DESIGN.md)** |
**[Changelog](https://github.com/nimashoghi/nshconfig/blob/main/CHANGELOG.md)**

## Install

Version 2 is currently an alpha release, so opt in to pre-releases explicitly:

```bash
python -m pip install --pre 'nshconfig>=2.0.0a0,<3'
python -m pip install --pre 'nshconfig[treescope]>=2.0.0a0,<3'   # rich notebook rendering
python -m pip install --pre 'nshconfig[transport]>=2.0.0a0,<3'   # cloudpickle transport
```

`nshconfig` supports Python 3.10 through 3.14 (package metadata excludes 3.15
until it is supported) and Pydantic 2.13 through the latest Pydantic 2.x release.

## Quick start

```python
from pathlib import Path

from pydantic import Field

import nshconfig as C


class Optimizer(C.Config):
    learning_rate: float = Field(default=3e-4, gt=0)


class LayerNorm(C.Config):
    dim: int = C.interp(lambda context: context.parent(Model).dim)


class Model(C.Config):
    dim: int = 768
    norm: LayerNorm


class Run(C.Config):
    optimizer: Optimizer
    model: Model


work = C.draft(Run)
with C.source("large-model sweep"):
    work.model.dim = 1024
    work.optimizer.learning_rate = 1e-4

run = C.finalize(work)
assert run.model.norm.dim == 1024

print(C.explain(run, "model.norm.dim"))
print(C.fingerprint(run))

run_record = C.record(run)
Path("run-config.json").write_text(run_record.to_json(indent=2))
```

Pydantic still owns schemas, aliases, validators, constraints, serialization, and
JSON Schema. Import those APIs from `pydantic`; `nshconfig` owns only its lifecycle,
interpolation, provenance, and run-record functions.

Drafts are real instances of their config class, so normal field access remains
visible to editors and type checkers. Required fields whose annotation is one
concrete `Config` subclass auto-create as child drafts. Drafts cannot be serialized.
`finalize()` is non-destructive, so one draft can produce many sweep variants.

Direct `Config` fields must be required or use an `interp()` default. Concrete
instances, mappings, `None`, and default factories would create a child without an
explicit parent interpolation context, so class creation rejects them. Config
validation accepts mappings and `Config` instances, not attribute-based objects.
Named type aliases and string-discriminated unions preserve structural annotations;
callable union discriminators are rejected.

Interpolation reads a completed nested `Config` through a read-only view that
exposes declared fields only, not Pydantic methods, private state, or custom
attributes. Container slicing and arithmetic preserve protected elements and record
truthful whole-container and origin dependencies. Config classes may use `before`
and `after` field or model validators.
Model `wrap`, field `plain`/`wrap`, and deprecated validator decorators are rejected
because they can bypass or repeat the one-pass field lifecycle.

Finals are ordinary validated Pydantic models with field assignment disabled. This
freeze is shallow: a nested `list` or `dict` retains normal Python mutability. Every
`Config` is unhashable; use `fingerprint()` for deterministic content identity.
Calling `finalize()` on a final revalidates its concrete values into a fresh final,
preserves `model_fields_set` and provenance, and does not rerun interpolation. The
source graph is isolated before validators run, including failure paths. Finals with
identity-bearing mutable atomic values are not safely revalidatable; rebuild those
from a draft or mapping.

## Composition, transport, and records

Keep the original draft when you need to change inputs and re-run interpolation.
A final or JSON record contains concrete values, not the Python composition recipe.

Use cloudpickle for trusted, short-lived transport of notebook-defined classes and
drafts with interpolation callables. Use `record()` for durable run metadata. A v4
run record stores canonical JSON values, provenance, the concrete config type, a
schema fingerprint, an exact-runtime `semantic_fingerprint`, and a verified value
fingerprint. The value fingerprint binds the canonical values to both fingerprints.
Dictionary insertion order is preserved and affects identity. `load_record()`
validates the stored values without running interpolation. Fingerprinting first
verifies that Pydantic consumes every field and reconstructs the same runtime meaning
and JSON values, so excluded fields and lossy serializers are rejected.

The schema fingerprint covers presentation-stripped canonical and alias validation
and serialization schemas. It also records aliases for every reachable nested model
and the record identity of every reachable nested `Config`. It cannot detect a
validator or serializer implementation change that leaves those contracts unchanged.
A generated generic parameterization must declare a distinct `record_schema_id`; a
uniquely named concrete subclass already has a stable `module:qualname` identity and
may declare an ID to version behavior that JSON Schema cannot express. Run-record
interpolation provenance uses structural integrity tokens, rejects opaque or stale
dependency claims and impossible declaration-order edges, and requires interpolation
to be the last event for its field.
Synchronize access while fingerprinting or recording; concurrent mutation is
unsupported and observed changes fail loudly.

Structural config positions must be explicitly annotated. Lifecycle checks cover
`Config` nodes and exact built-in `dict`, `list`, `tuple`, `set`, and `frozenset`
containers. Dataclasses, ordinary Pydantic models, and other non-collection user
objects are atomic and cannot hide `Config` nodes or pending interpolation. Known
carrier objects, including functions, bound methods, partials, and weak references,
are inspected. Opaque callables are rejected when their captured state cannot be
inspected; other truly opaque extension state cannot be proven safe. A model-before
validator may normalize custom input, but any custom collection or lazy synchronous
or asynchronous input left afterward is rejected; materialize and await it first.
A run record's provenance must be self-contained under the recorded root.
Interpolation contexts and views expire with their resolver and cannot cross thread
boundaries. Arbitrary mapping keys use a truthful whole-mapping dependency when the
value below the key contains no `Config` structure.

See the
[semantic contract](https://github.com/nimashoghi/nshconfig/blob/main/DESIGN.md)
for the complete behavior, including validation order and the supported value graph.

## License

MIT
