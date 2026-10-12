# Upgrade to v3

Version 3.0.0 is the first stable v3 release. It intentionally replaces the older configuration API. There is no compatibility layer for 0.x or v2. Review configuration construction, application entry points, and exports before upgrading an existing application.

## Construct drafts and finalize at the application boundary

Both keyword constructors and `.draft()` create editable drafts. Constructors have checked keyword signatures; `.draft()` permits incomplete construction. Pass a finalized snapshot to application code:

```python
import nshconfig as C

class Run(C.Config):
    name: str
    width: int = 128

config = Run.draft()
config.name = "experiment"
config.width = 256
ready = config.finalize()

config.width = 384
assert ready.width == 256
```

The v2 `config_draft` and `config_finalize` methods are removed. Use `.draft()` and `.finalize()`. A normal constructor also requires explicit finalization. Compose presets with ordinary Python functions.

## Update validation and interpolation

Use `Annotated` with `Field`, `BeforeValidator`, or `AfterValidator` for field-local validation. Writes defer value validation; reads validate requested values and their dependencies. Validators can normalize leaves but must preserve container shape and Config identities. Use interpolation or builder code to change the structure.

Use `@C.check` methods for cross-field checks. Checks run on the final snapshot and return `None`; they cannot rewrite values. Interpolation uses `Context.root`, `nearest`, or `current` and follows dependencies rather than declaration order.

nshconfig is not a Pydantic `BaseModel`. The v2 template lifecycle, unrestricted Pydantic authoring re-exports, and treescope integration are removed. See the [contract](../contract.md) for the supported API.

## Check ownership and external boundaries

Each child Config has one owner. Use `.copy()` before reusing a child in another tree. Class-declared defaults are copied automatically. Final snapshots and computed subtrees reject nested mutations; copy them to obtain an editable draft.

Runtime lists and dictionaries are managed sequence/mapping objects. Use a final's `.to_dict()` or `.to_json()` when exporting a configuration. Use `list(...)` or `dict(...)` when an external API requires concrete built-in containers. Mutable opaque objects and mutable sets are unsupported configuration values.

## Upgrade from the v3 alpha

The stable release preserves the v3 lifecycle. It fixes tuple normalization that could bypass validation and ownership, annotation resolution for nested classes and class-local aliases, aliased or quoted `ClassVar` declarations, and inline lambda validators on Python 3.14. Validators that introduced new container structure through a tuple now raise `ConfigError`; move that construction into a builder or interpolation callback.
