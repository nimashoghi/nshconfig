"""Golden typing probe: each seeded mistake below MUST be flagged by basedpyright.

The expected (line, rule) pairs are asserted by tests/test_typing.py; keep the
markers and this file's line numbers in sync via the BAD: comments.
"""

from __future__ import annotations

import nshconfig as C


class LNConfig(C.Config):
    dim: int = 32


class ModelConfig(C.Config):
    dim: int = 768
    ln: LNConfig


bad_default: int = C.interp(lambda c: "oops")  # BAD: lambda return type vs annotation

cfg = ModelConfig.draft()
cfg.ln.dim = C.interp(lambda c: "oops")  # BAD: lambda return type at assignment site
cfg.ln.dmi = 3  # BAD: unknown attribute on a draft
cfg.dim = "1024"  # BAD: wrong value type at assignment site
