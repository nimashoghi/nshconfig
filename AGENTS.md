# nshconfig v3

Read `DESIGN.md` before changing semantics. It is the canonical contract; `README.md` introduces the API, and `SKILL.md` guides consumers. Intentional behavior changes must update those documents and public tests together. There is no v2 compatibility requirement.

The implementation separates field-local Pydantic validation from nshconfig's ownership, resolution, and snapshot lifecycle. Preserve raw inputs separately from canonical values, atomic ownership changes, live interpolation, and independent recursively read-only finals. Public names are explicitly re-exported from `src/nshconfig/__init__.py`.

Python 3.10-3.14, eager/quoted/postponed annotations, and notebook-defined class transport are required. Subprocess cloudpickle tests and positive/negative ty, Pyright, and mypy fixtures are release checks. Keep library source free of basedpyright errors and warnings.

Use `uv sync --locked --all-groups --all-extras` for development. The release check sequence is executable in `scripts/publish.sh`; run it only for an intentional release. Individual checks are pytest, nox, basedpyright, ruff, Sphinx, and distribution smoke tests as listed there.
