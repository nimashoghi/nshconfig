from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from . import cached_path as cached_path
    from .cached_path import CachedPath as CachedPath
else:

    def __getattr__(name: str):
        match name:
            case "cached_path":
                from . import cached_path as cached_path

                return cached_path
            case "CachedPath":
                from .cached_path import CachedPath as CachedPath

                return CachedPath
            case _:
                raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
