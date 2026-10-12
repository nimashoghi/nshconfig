# Typing

nshconfig uses standard annotations and `dataclass_transform`. ty, Pyright, and mypy check synthesized keyword constructors, field assignment, nested access, contextual selectors, and interpolation return types. No plugin or generated stub is needed.

Bare annotations declare required fields. Normal constructors require them statically. `.draft()` takes no arguments and returns the exact subclass type while permitting incremental initialization. Constructors and `.draft()` share draft runtime semantics.

Drafts and finals have the same public schema type. Completeness and read-only state are runtime guarantees, not separate static types. Managed sequences/mappings retain familiar list/dict annotations but are wrapper objects at runtime.

Use eager, quoted, or postponed annotations. Module-level forward references resolve once their definitions exist. For late function-local types, call `Schema.rebuild(namespace={"Child": Child})` after defining them.

Annotations use the declaring class and module namespaces, including nested schema classes and class-local type aliases. `ClassVar` declarations remain class variables, including imported aliases and quoted annotations. Explicit `rebuild(namespace=...)` entries override captured and class-local names. Inline validators also work with Python 3.14's native deferred annotations.

The development test suite checks positive fixtures and exact expected error lines across all three checkers. Library implementation typing is checked separately with basedpyright.
