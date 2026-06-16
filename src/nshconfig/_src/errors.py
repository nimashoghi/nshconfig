"""Error types and shared rendering helpers for the v2 core.

Every failure names the dotted instance path where it happened, the owning
``Cls.field``, and, when a marker is involved, the marker's source site captured
at construction.
"""

__all__ = ["DraftError", "UnsetError"]


class UnsetError(AttributeError):
    """Reading a draft field that has no value yet (unset, or pending interpolation)."""


class DraftError(TypeError):
    """A draft was used where only a finalized config is meaningful."""
