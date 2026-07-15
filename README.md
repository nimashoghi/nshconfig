# nshconfig

Typed, Python-first configuration for ML runs, built on
[Pydantic](https://docs.pydantic.dev/).

`nshconfig` adds two ideas to ordinary Pydantic models:

- explicit mutable drafts for assembling incomplete configuration;
- declaration-ordered Python interpolation over canonical validated values.

There is no YAML language, registry, loader, code generator, provenance layer, or
Pydantic re-export layer.

**[Documentation](https://nima.sh/nshconfig/)** | **[Semantic design](https://github.com/nimashoghi/nshconfig/blob/main/DESIGN.md)**

## Install

```bash
pip install --pre nshconfig
pip install --pre 'nshconfig[treescope]'   # rich notebook rendering
pip install --pre 'nshconfig[transport]'   # trusted cloudpickle transport
```

`nshconfig` supports Python 3.10 through 3.14 and Pydantic 2.13 through the
latest Pydantic 2.x release.

## Two construction modes

Calling a config class has ordinary Pydantic meaning and returns a validated,
field-frozen final:

```python
import nshconfig as C


class Child(C.Config):
    x: int = 1
    y: int = 2


class Parent(C.Config):
    child: Child = Child()


final = Parent(child=Child(x=10, y=20))
assert not C.is_draft(final)
```

Composition uses an explicit draft and one validation boundary:

```python
work = Parent.config_draft()
assert C.is_draft(work.child)

work.child.x = 10
final = work.config_finalize()

assert final == Parent(child=Child(x=10, y=2))
assert C.is_draft(work)  # finalization is non-destructive
```

A normally constructed `Config` default is a template. When its parent becomes a
draft, default-origin children become fresh drafts recursively through annotated
lists, tuples, mappings, unions, and TypedDict values. An explicitly assigned
final remains a final.

Required fields with one concrete `Config` annotation lazily create child drafts.
Reading another unset required field raises `UnsetError`. Draft writes are not
validated until `config_finalize()`.

## Interpolation

`interp()` derives one complete field value. Pydantic field declaration order is
dependency order:

```python
from pydantic import Field


class Norm(C.Config):
    dim: int = C.interp(lambda context: context.parent(Model).dim)


class Model(C.Config):
    dim: int = 768
    norm: Norm = Field(default_factory=Norm.config_draft)


assert Model().norm.dim == 768
```

The callable may use `context.current()`, `parent()`, `root()`, or
`nearest(ConfigType)`. It sees only earlier fields whose complete Pydantic field
validation has finished. The interpolation result then runs through the target
field's normal validation pipeline.

Use `Field(default_factory=Child.config_draft)` when a default child needs its
parent's interpolation context. A direct `Child()` default must be valid on its
own when the parent class body executes.

## Project composition convention

Keep reusable config builders under `src/project/configs/` as ordinary in-place
mutators:

```python
def resnet50(cfg: ModelConfig, *, d_model: int = 256) -> ModelConfig:
    cfg.d_model = d_model
    return cfg
```

Root files under `configs/` use the same contract:

```python
def __config__(cfg: TrainConfig) -> TrainConfig:
    resnet50(cfg.model)
    cfg.seed = 7
    return cfg
```

The application owns loading. It creates the expected root draft, calls
`__config__`, verifies that the returned object is the identical draft, and calls
`config_finalize()` exactly once. `nshconfig` intentionally provides no loader or
registry.

## Pydantic behavior

Pydantic owns fields, aliases, validators, constraints, serialization, JSON
Schema, and normal constructors. Import those APIs directly from `pydantic`.

The base config is strict, forbids extras, validates defaults, revalidates model
instances, and is shallowly field-frozen. A project base class may change policy
such as strictness, but not lifecycle settings.

Model validators retain native Pydantic semantics. Model-after hooks may mutate
or replace values, which can make an interpolated relationship stale. Likewise,
`final.model_copy(update=...)` does not validate its updates. To validate the
current concrete contents of a final, use:

```python
checked = type(final).model_validate(final)
```

Drafts cannot be copied or serialized through Pydantic or JSON. Trusted pickle
transport is the explicit exception described below. Finals use value equality
and the same field-value hashing rule as frozen Pydantic models: they are hashable
exactly when all field values are hashable. Freezing is shallow, so lists,
dictionaries, sets, and arbitrary objects retain ordinary Python mutability.

The public package API is `Config`, `Context`, `interp`, `is_draft`, `DraftError`,
and `UnsetError`, plus `__version__`.

## Trusted executable transport

Cloudpickle can transport notebook-local classes, drafts, and interpolation
callables between compatible trusted environments. Pickle data can execute code;
never load it from an untrusted source. A final contains concrete values and cannot
recreate the original draft recipe.

See the [semantic design](https://github.com/nimashoghi/nshconfig/blob/main/DESIGN.md)
for the complete lifecycle and validation contract.

## License

MIT
