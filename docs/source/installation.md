# Installation

```bash
pip install 'nshconfig==3.0.0a0'
```

Python 3.10-3.14 and Pydantic 2.13 through the latest 2.x are supported. For trusted notebook transport, install `nshconfig[transport]` with the same version constraint. ty, Pyright, and mypy require no nshconfig plugins.

v3 intentionally changes the v2 lifecycle and API. Constructors now make drafts; use `.draft()` for incomplete construction and `.finalize()` for completion. There are no unbound templates or v2 compatibility aliases.
