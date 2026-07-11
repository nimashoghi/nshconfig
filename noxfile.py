"""Test every supported Python against the Pydantic floor and latest 2.x."""

import nox

nox.options.default_venv_backend = "uv"

PYTHON_VERSIONS = ["3.10", "3.11", "3.12", "3.13", "3.14"]
PYDANTIC_REQUIREMENTS = ["pydantic==2.13.0", "pydantic>=2.13,<3"]


@nox.session(python=PYTHON_VERSIONS)
@nox.parametrize("pydantic", PYDANTIC_REQUIREMENTS, ids=["floor", "latest"])
def tests(session: nox.Session, pydantic: str) -> None:
    session.install(
        "-e",
        ".[transport,treescope]",
        "pytest",
        "pytest-cov",
        "basedpyright==1.36.2",
        pydantic,
    )
    session.run("pytest", "-q", "-p", "no:cacheprovider")
