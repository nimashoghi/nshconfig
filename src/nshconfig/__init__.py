"""nshconfig v2: typed, provenance-aware configuration for ML runs.

Three verbs and one value:

- ``Cls.draft()`` makes a mutable draft (plain assignment, auto-vivifying nesting).
- ``C.interp(lambda c: ...)`` is a VALUE that resolves against the config tree at
  validation, legal anywhere a value sits (draft assignment, input dict, class default).
- ``C.finalize(draft)`` resolves, validates once, and returns a frozen final.

Plus provenance: ``C.explain(cfg, "optim.lr")`` answers "why did this run use that
value", down to file:line and the interpolation's "because" chain.
"""

from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version

from ._src.config import Config as Config
from ._src.config import is_draft as is_draft
from ._src.errors import DraftError as DraftError
from ._src.errors import UnsetError as UnsetError
from ._src.finalize import finalize as finalize
from ._src.finalize import thaw as thaw
from ._src.interp import Ctx as Ctx
from ._src.interp import Interp as Interp
from ._src.interp import interp as interp
from ._src.provenance import Event as Event
from ._src.provenance import Explanation as Explanation
from ._src.provenance import explain as explain
from ._src.provenance import provenance as provenance
from ._src.provenance import source as source

try:
    __version__ = version(__name__)
except PackageNotFoundError:
    __version__ = "unknown"

__all__ = [
    "Config",
    "Ctx",
    "DraftError",
    "Event",
    "Explanation",
    "Interp",
    "UnsetError",
    "explain",
    "finalize",
    "interp",
    "is_draft",
    "provenance",
    "source",
    "thaw",
    "__version__",
]
