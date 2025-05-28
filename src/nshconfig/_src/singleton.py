from __future__ import annotations

import logging
import threading
import warnings
from typing import Any, Generic, overload

from typing_extensions import TypeVar, override

log = logging.getLogger(__name__)

T = TypeVar("T", infer_variance=True)


class Singleton(Generic[T]):
    """Manages singleton access to a configuration class using the descriptor pattern.

    This class provides a way to access a singleton instance of a configuration
    class through a descriptor that automatically binds to the owner class.

    Example:
        ```python
        class MyConfig(Config):
            singleton: ClassVar[Singleton[Self]] = Singleton()
            attribute: str
        ```

    Inheritance behavior:
    - A derived class must define its own Singleton instance
    - To share a base class's singleton, explicitly assign it:
      `singleton = BaseClass.singleton`
    """

    def __init__(self) -> None:
        """Initialize the Singleton."""
        self._instance: T | None = None
        self._lock = threading.RLock()
        self._owner_cls: type[T] | None = None
        self._reference_classes: set[type[T]] = set()

    def __set_name__(self, owner: type[T], name: str) -> None:
        """Automatically bind to the owner class when assigned as a class variable."""
        if self._owner_cls is None:
            self._owner_cls = owner
        else:
            self._reference_classes.add(owner)

    def __get__(self, instance: Any, owner: type[T]) -> Singleton[T]:
        """Return the singleton instance for the owner class."""
        self._check_class_access(owner)
        return self

    def instance(self) -> T:
        """Get the singleton instance.

        Returns:
            The singleton instance of the configuration class.

        Raises:
            RuntimeError: If no instance exists yet.
            TypeError: If accessed from a derived class that doesn't explicitly assign this singleton.
        """
        self._check_class_access(None)

        if (instance := self._instance) is None:
            raise RuntimeError(
                "No instance has been initialized. Call initialize() first."
            )
        return instance

    def try_instance(self) -> T | None:
        """Try to get the singleton instance if it exists.

        Returns:
            The singleton instance of the configuration class or None if not initialized.

        Raises:
            TypeError: If accessed from a derived class that doesn't explicitly assign this singleton.
        """
        self._check_class_access(None)

        return self._instance

    @overload
    def initialize(self, instance: T, /) -> T: ...

    @overload
    def initialize(self, **kwargs: Any) -> T: ...

    def initialize(self, *args: Any, **kwargs: Any) -> T:
        """Initialize the singleton instance.

        Can either directly set an existing instance or create a new instance
        with the provided kwargs.

        Args:
            instance: An existing instance to use as the singleton (positional only).
            **kwargs: Keyword arguments to initialize a new instance (used only if
                      no positional argument is provided).

        Returns:
            The singleton instance of the configuration class.

        Raises:
            RuntimeWarning: If an instance already exists.
            TypeError: If neither instance nor kwargs are provided, or if accessed
                      from a derived class that doesn't explicitly assign this singleton.
        """
        self._check_class_access(None)

        # Quick check without acquiring the lock
        if self._instance is not None:
            warnings.warn(
                "Singleton instance already exists. "
                "The existing instance will be returned.",
                RuntimeWarning,
                stacklevel=2,
            )
            return self._instance

        # Ensure we have the managed class
        if self._owner_cls is None:
            raise TypeError(
                "Singleton is not correctly bound to a class. "
                "Ensure it is assigned as a ClassVar to the configuration class."
            )

        # If not found, acquire the lock for thread-safe initialization
        with self._lock:
            # Check again in case another thread created the instance
            if (instance := self._instance) is None:
                if len(args) == 1:
                    # Use the provided instance directly
                    instance = args[0]
                elif kwargs:
                    # Create a new instance with the provided kwargs
                    instance = self._owner_cls(**kwargs)
                else:
                    raise TypeError(
                        "Either an instance or initialization parameters must be provided."
                    )
                self._instance = instance

            return instance

    def reset(self) -> None:
        """Reset the singleton instance.

        This allows re-initialization with different parameters.

        Raises:
            TypeError: If accessed from a derived class that doesn't explicitly assign this singleton.
        """
        self._check_class_access(None)

        with self._lock:
            self._instance = None

    def _check_class_access(self, cls: type[T] | None) -> None:
        """Check if the class accessing this singleton is the owner or has explicitly assigned it."""
        if self._owner_cls is None:
            raise TypeError(
                "Singleton is not correctly bound to a class. "
                "Ensure it is assigned as a ClassVar to the configuration class."
            )

        if cls is not None and cls is not self._owner_cls:
            # Check for the behavior like the following:
            # class DerivedConfig(BaseConfig):
            #     singleton = BaseConfig.singleton
            # This allows derived classes to access the base class's singleton
            # without raising an error.
            if cls in self._reference_classes:
                log.info(
                    f"Class {cls.__name__} is accessing a singleton from {self._owner_cls.__name__}."
                )
            # Otherwise, raise an error if the class is not the owner or a reference
            # class.
            else:
                raise TypeError(
                    f"Class {cls.__name__} is not allowed to access the singleton of {self._owner_cls.__name__}."
                    " Ensure it is explicitly assigned in the derived class."
                )

    @override
    def __repr__(self) -> str:
        """Representation of the Singleton descriptor."""
        owner_name = self._owner_cls.__name__ if self._owner_cls else None
        status = "initialized" if self._instance is not None else "uninitialized"
        return f"<{self.__class__.__name__} owner={owner_name}, status={status}, instance={self._instance}>"
