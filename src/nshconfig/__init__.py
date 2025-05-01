from __future__ import annotations

from ._src.adapter import Adapter as Adapter
from ._src.config import Config as Config
from ._src.config import ConfigDict as ConfigDict
from ._src.config import with_config as with_config
from ._src.export import Export as Export
from ._src.invalid import Invalid as Invalid
from ._src.missing import MISSING as MISSING
from ._src.missing import AllowMissing as AllowMissing
from ._src.missing import validate_no_missing as validate_no_missing
from ._src.pydantic_exports import *
from ._src.registry import Registry as Registry
from ._src.registry import RegistryConfig as RegistryConfig
from ._src.root import CLI as CLI
from ._src.root import CliApp as CliApp
from ._src.root import RootConfig as RootConfig
from ._src.root import RootConfigDict as RootConfigDict
from ._src.utils import deduplicate as deduplicate
from ._src.utils import deduplicate_configs as deduplicate_configs

try:
    from importlib.metadata import PackageNotFoundError, version
except ImportError:
    # For Python <3.8
    from importlib_metadata import (  # pyright: ignore[reportMissingImports]
        PackageNotFoundError,
        version,
    )

try:
    __version__ = version(__name__)
except PackageNotFoundError:
    __version__ = "unknown"
