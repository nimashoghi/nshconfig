"""Typed Python configuration with explicit drafts and interpolation."""

from importlib.metadata import PackageNotFoundError as _PackageNotFoundError
from importlib.metadata import version as _version

from ._src.config import Config as Config
from ._src.errors import DraftError as DraftError
from ._src.errors import UnsetError as UnsetError
from ._src.interp import Context as Context
from ._src.interp import interp as interp
from ._src.state import is_draft as is_draft

try:
    __version__ = _version(__name__)
except _PackageNotFoundError:
    __version__ = "unknown"

__all__ = [
    "Config",
    "Context",
    "DraftError",
    "UnsetError",
    "__version__",
    "interp",
    "is_draft",
]
