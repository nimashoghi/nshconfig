from typing import TYPE_CHECKING, ClassVar

from pydantic_settings import BaseSettings
from pydantic_settings import SettingsConfigDict as _SettingsConfigDict
from typing_extensions import Unpack

from ._config import Config


class SettingsConfigDict(_SettingsConfigDict, total=False):
    repr_diff_only: bool
    """
    If `True`, the repr methods will only show values for fields that are different from the default.
    Defaults to `False`.
    """


class Settings(BaseSettings, Config):
    model_config: ClassVar[SettingsConfigDict] = SettingsConfigDict(  # type: ignore
        # By default, Pydantic will throw a warning if a field starts with "model_",
        # so we need to disable that warning (beacuse "model_" is a popular prefix for ML).
        protected_namespaces=(),
        validate_assignment=True,
        validate_return=True,
        validate_default=True,
        strict=True,
        revalidate_instances="always",
        arbitrary_types_allowed=True,
        extra="ignore",
        validation_error_cause=True,
        use_attribute_docstrings=True,
    )

    if TYPE_CHECKING:

        def __init_subclass__(cls, **kwargs: Unpack[SettingsConfigDict]):
            super().__init_subclass__(**kwargs)
