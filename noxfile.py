"""Nox: test the support matrix. Pydantic floor (2.13) and latest, per Python."""

import nox

nox.options.default_venv_backend = "uv"

PYTHON_VERSIONS = ["3.10", "3.11", "3.12", "3.13"]
PYDANTIC_PINS = ["pydantic>=2.13,<2.14", "pydantic"]  # floor minor, then latest


@nox.session(python=PYTHON_VERSIONS)
@nox.parametrize("pydantic", PYDANTIC_PINS, ids=["floor", "latest"])
def tests(session: nox.Session, pydantic: str) -> None:
    session.install(
        "-e",
        ".[treescope]",
        "pytest",
        "pytest-cov",
        "cloudpickle",
        "basedpyright",
        pydantic,
    )
    session.run("pytest", "-q", "-p", "no:cacheprovider")
