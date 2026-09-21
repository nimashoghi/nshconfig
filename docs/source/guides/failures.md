# Failures

- `MissingValueError`: a requested required field is unset.
- `InterpolationError`: contextual selection failed or a dependency cycle was detected.
- `OwnershipError`: attachment would create multiple owners or a structural cycle.
- `FrozenError`: a final/computed value was mutated, or a callback tried to mutate its resolving tree.
- `ConfigError`: field validation failed, export preceded finalization, or a value/normalization violated the config graph contract.

Unknown attributes raise `AttributeError`; unknown constructor keywords raise `TypeError`. User callbacks and checks retain their own exceptions. A failed finalization leaves the draft available for correction. Ownership changes validate the proposed attachment before modifying existing ownership.
