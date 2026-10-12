# Changelog

## 3.0.0

First stable v3 release: typed editable drafts, live contextual interpolation, and independent recursively read-only final snapshots. Python 3.10-3.14 and Pydantic 2.13 through the latest 2.x are supported.

Changes from `3.0.0a0`:

- Reject tuple normalization that introduces new containers or Config nodes and bypasses validation, ownership, and snapshot-copy guarantees.
- Resolve nested schemas and class-local type aliases in the declaring namespace, with explicit rebuild overrides.
- Recognize aliased and quoted `ClassVar` annotations as class variables.
- Preserve native Python 3.14 annotation objects, including inline lambda validators.
- Expand regression and subprocess transport coverage for these annotation forms.
- Correct stale agent instructions and add an upgrade guide.

This release intentionally breaks compatibility with 0.x and v2. Constructors create drafts; use `.finalize()` before application execution. There are no compatibility aliases. Read the [upgrade guide](https://nima.sh/nshconfig/guides/upgrading.html) and [design contract](DESIGN.md) before migrating.
