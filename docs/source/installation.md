# Installation

```bash
pip install nshconfig
```

Requirements: Python >= 3.10 and pydantic >= 2.13. The only runtime dependencies are
`pydantic` and `typing-extensions`.

Optional extras:

```bash
pip install nshconfig[treescope]   # rich notebook rendering of configs and drafts
```

For the notebook-to-cluster workflow (pickling pending drafts with lambda interpolations),
install [cloudpickle](https://github.com/cloudpipe/cloudpickle) on both ends; plain pickle
covers finals and named-function interpolations.

The supported type checker is [basedpyright](https://docs.basedpyright.com/). pydantic's mypy
plugin flags the draft-mutation idiom (writes to frozen-typed classes), which basedpyright
deliberately permits via `model_config`-level frozen; see the [typing guide](guides/typing.md).
