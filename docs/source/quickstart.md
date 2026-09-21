# Quickstart

```python
import nshconfig as C

class Model(C.Config):
    width: int = C.interp(lambda c: c.root(Run).width)

class Run(C.Config):
    name: str
    width: int = 128
    model: Model = Model()

config = Run.draft()
config.name = "example"
config.width = 256
assert config.model.width == 256
ready = config.finalize()
config.width = 512
assert ready.model.width == 256
```

`Run(name="example", width=256)` is the short form. It also creates an editable draft. Normal constructors have statically checked required arguments; `.draft()` deliberately permits required fields to be unset. Reading an unset required field fails at runtime.

Call your ordinary Python application function with the finalized config. Use `ready.to_dict()` or `ready.to_json()` for exports.
