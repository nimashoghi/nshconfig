# Installation

Version 2 is currently an alpha release. Opt in to pre-releases explicitly when
installing the core package:

```bash
python -m pip install --pre 'nshconfig>=2.0.0a0,<3'
```

`nshconfig` supports Python 3.10 through 3.14; package metadata excludes Python
3.15 until it is supported. Pydantic 2.13 through the latest Pydantic 2.x release
is supported. The core dependencies are `pydantic` and `typing-extensions`.

Install optional features only where they are needed:

```bash
python -m pip install --pre 'nshconfig[treescope]>=2.0.0a0,<3'   # rich notebook rendering
python -m pip install --pre 'nshconfig[transport]>=2.0.0a0,<3'   # cloudpickle transport
python -m pip install --pre 'nshconfig[docs]>=2.0.0a0,<3'        # local documentation builds
```

Pydantic remains the schema API. Import `Field`, `ConfigDict`, validators, and
other Pydantic features directly:

```python
from pydantic import ConfigDict, Field, field_validator

import nshconfig as C
```

[basedpyright](https://docs.basedpyright.com/) is the supported type checker. See
the [typing guide](guides/typing.md) for the checked and runtime-only parts of the
contract.

Install the transport extra on both sides of a notebook-to-cluster transfer. The
environments need the same Python version and compatible dependencies; see
[transport, security, and environments](guides/transport.md).
