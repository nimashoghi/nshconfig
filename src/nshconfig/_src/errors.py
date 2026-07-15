"""Public exceptions raised by nshconfig's lifecycle boundaries."""

__all__ = ["DraftError", "UnsetError"]


class UnsetError(AttributeError):
    """A draft field was read before it had a concrete value."""


class DraftError(TypeError):
    """A draft was used where a validated final is required."""
