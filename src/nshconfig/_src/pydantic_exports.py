from __future__ import annotations

from typing import TYPE_CHECKING, cast

import pydantic as pydantic  # full module export
from pydantic import *  # pyright: ignore[reportWildcardImportFromLibrary]
from typing_extensions import Never

# Delete the BaseModel and create_model imports from pydantic
if TYPE_CHECKING:
    BaseModel = cast(Never, None)
    create_model = cast(Never, None)
else:
    del BaseModel
    del create_model

# Re-export ConfigDict and with_config from our implementation
if not TYPE_CHECKING:
    del ConfigDict
    try:
        del with_config
    except NameError:
        pass
from .config import ConfigDict as ConfigDict
from .config import with_config as with_config
