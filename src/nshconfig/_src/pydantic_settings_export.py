from __future__ import annotations

from typing import TYPE_CHECKING, cast

import pydantic_settings as pydantic_settings  # full module export
from pydantic_settings import *  # pyright: ignore[reportWildcardImportFromLibrary]
from typing_extensions import Never

# Delete the BaseSettings import from pydantic_settings
if TYPE_CHECKING:
    BaseSettings = cast(Never, None)
else:
    del BaseSettings

# Re-export SettingsConfigDict and CliApp from our implementation
if not TYPE_CHECKING:
    try:
        del CliApp
    except NameError:
        pass
    del SettingsConfigDict
from .root import CliApp as CliApp
from .root import RootConfigDict as RootConfigDict
from .root import RootConfigDict as SettingsConfigDict
