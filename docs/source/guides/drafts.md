# Drafts, ownership, and snapshots

Compose a draft through ordinary field assignments and Python helpers. Writes defer value validation, but unknown field names and invalid ownership fail immediately. Field reads validate requested values. Ordinary child access does not complete that child's required fields.

Both constructors and `.draft()` return editable configs. `.finalize()` returns a separate completed snapshot and leaves the original draft's rules intact. Final config fields and managed containers reject mutation. `.copy()` returns an unattached editable copy. A draft copy preserves interpolation rules; a final copy has concrete values.

Explicit child assignment preserves identity and permits one owner. Copy a child to reuse it. Declared defaults, including child Config defaults, are copied per parent. Replacing or removing a child detaches it; deleting a field unsets it rather than restoring its default.

Plain incoming lists/dicts are copied into managed containers. Mutating their original external containers does not edit the config. Aliases obtained from a config stay live while attached. Supported mutation includes indexing, slicing, append/extend, sorting, and mapping updates. Config elements have the same single-owner rule.

Managed values implement sequence/mapping interfaces, not built-in list/dict identity. Convert at external boundaries with `list`, `dict`, or a final's `.to_dict()`. Tuples can contain managed containers and Config nodes. Arbitrary mutable leaves and mutable sets are unsupported; use Config, list, dict, and tuple to describe mutable configuration state.
