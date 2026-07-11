"""Public exceptions raised by nshconfig's lifecycle boundaries."""

__all__ = ["DraftError", "FingerprintError", "RecordError", "UnsetError"]


class UnsetError(AttributeError):
    """A draft field was read before it had a concrete value."""


class DraftError(TypeError):
    """A draft was used where a validated final is required."""


class FingerprintError(ValueError):
    """A final cannot be converted to deterministic fingerprint data."""


class RecordError(ValueError):
    """A run record is malformed or does not match its declared config."""
