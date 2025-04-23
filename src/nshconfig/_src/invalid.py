from __future__ import annotations

from typing import Any

from pydantic import model_validator

from .config import Config


class Invalid(Config):
    """
    A class representing an invalid configuration.

    This is like the Never type (which isn't supported by Pydantic).
    """

    @model_validator(mode="before")
    @classmethod
    def invalidate(cls, data: Any) -> Any:
        raise ValueError("This is an invalid configuration.")
