# Installation

Install the core package:

```bash
pip install --pre nshconfig
```

The `--pre` flag selects the current v2 prerelease instead of the older stable
series. It will no longer be needed once v2 is released as stable.

`nshconfig` supports Python 3.10 through 3.14 and Pydantic 2.13 through the
latest Pydantic 2.x release. Its core dependencies are `pydantic` and
`typing-extensions`.

Install optional features only where they are needed:

```bash
pip install --pre 'nshconfig[treescope]'   # rich notebook rendering
pip install --pre 'nshconfig[transport]'   # cloudpickle transport
pip install --pre 'nshconfig[all]'         # both optional features
```

Pydantic remains the schema API. nshconfig re-exports its non-deprecated
authoring surface unchanged, so application schemas need only one import:

```python
import nshconfig as C


class Job(C.Config):
    model_config = C.ConfigDict(strict=False)
    workers: int = C.Field(gt=0)

    @C.field_validator("workers")
    @classmethod
    def check_workers(cls, value: int) -> int:
        return value
```

Direct imports from `pydantic` remain equivalent.

[basedpyright](https://docs.basedpyright.com/) is the supported type checker. See
the [typing guide](guides/typing.md) for the checked and runtime-only parts of the
contract.

Install the transport extra on both sides of a notebook-to-cluster transfer. The
environments need the same Python version and compatible dependencies; see
[transport, security, and environments](guides/transport.md).
