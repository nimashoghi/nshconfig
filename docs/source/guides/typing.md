# Typing

`Config` uses Pydantic's dataclass transform for ordinary construction. The two
lifecycle methods return `Self`:

```python
work = Model.config_draft()       # inferred as Model
final = work.config_finalize()    # inferred as Model
```

Static typing intentionally does not distinguish a draft from a final. The same
typed object flows through project mutators, while lifecycle misuse fails at
runtime.

```python
def resnet50(cfg: Model, *, dim: int = 256) -> Model:
    cfg.dim = dim
    return cfg
```

Draft field assignments are checked against declared field types. Runtime draft
assignment remains unvalidated, so a suppressed type error still fails during
finalization.

## Typed interpolation context

Pass the expected Config type to a selector for checked field access:

```python
class Leaf(C.Config):
    copied: int = C.interp(lambda context: context.parent(Model).dim)
```

`current(Model)`, `parent(Model)`, `parent(2, Model)`, `root(Run)`, and
`nearest(Model)` return the requested static type. Selector reachability and field
declaration order remain runtime properties.

## Annotation evaluation

Normal eager annotations, explicit quoted forward references, and
`from __future__ import annotations` are all supported across Python 3.10 through
3.14. The optional trusted cloudpickle transport also supports these forms for
notebook-local Config classes.

Name resolution otherwise follows Pydantic. If an annotation refers to a name
that is still unavailable when the class is created, define the name and call
`ConfigType.model_rebuild()` using the same rules as an ordinary Pydantic model.

Names used only inside an interpolation lambda are resolved when the callable
runs, so a later class may be referenced without turning the field annotation into
a string.

## Supported checker

[basedpyright](https://docs.basedpyright.com/) is the checked contract. The
repository includes positive and negative golden probes under
`tests/typing_probes/`. Diagnostic changes are treated as typing behavior changes.
