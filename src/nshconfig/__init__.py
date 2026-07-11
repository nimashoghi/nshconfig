"""Typed Python configuration with drafts, interpolation, and provenance."""

from importlib.metadata import PackageNotFoundError as _PackageNotFoundError
from importlib.metadata import version as _version

from ._src.config import Config as Config
from ._src.draft import draft as draft
from ._src.errors import DraftError as DraftError
from ._src.errors import FingerprintError as FingerprintError
from ._src.errors import RecordError as RecordError
from ._src.errors import UnsetError as UnsetError
from ._src.finalize import finalize as finalize
from ._src.interp import Context as Context
from ._src.interp import interp as interp
from ._src.provenance import Event as Event
from ._src.provenance import Explanation as Explanation
from ._src.provenance import explain as explain
from ._src.provenance import provenance as provenance
from ._src.provenance import source as source
from ._src.records import RunRecord as RunRecord
from ._src.records import fingerprint as fingerprint
from ._src.records import load_record as load_record
from ._src.records import record as record
from ._src.state import is_draft as is_draft

try:
    __version__ = _version(__name__)
except _PackageNotFoundError:
    __version__ = "unknown"

__all__ = [
    "Config",
    "Context",
    "DraftError",
    "Event",
    "Explanation",
    "FingerprintError",
    "RecordError",
    "RunRecord",
    "UnsetError",
    "__version__",
    "draft",
    "explain",
    "finalize",
    "fingerprint",
    "interp",
    "is_draft",
    "load_record",
    "provenance",
    "record",
    "source",
]
