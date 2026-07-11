#!/usr/bin/env bash

set -euo pipefail

repo_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
readonly repo_root
cd "$repo_root"

unset FORCE_COLOR
export NO_COLOR=1

readonly required_uv_version="0.11.28"
actual_uv_version="$(uv --version | awk '{print $2}')"
readonly actual_uv_version
if [[ "$actual_uv_version" != "$required_uv_version" ]]; then
    echo "Publishing requires uv $required_uv_version; found $actual_uv_version." >&2
    exit 1
fi

require_clean_worktree() {
    if [[ -n "$(git status --porcelain=v1 --untracked-files=all)" ]]; then
        echo "Refusing to publish from a dirty worktree." >&2
        exit 1
    fi
}

assert_release_tag() {
    local tag_commit
    if ! tag_commit="$(git rev-parse --verify "${expected_tag}^{commit}" 2>/dev/null)"; then
        echo "Release tag $expected_tag does not exist." >&2
        exit 1
    fi
    if [[ "$tag_commit" != "$(git rev-parse --verify HEAD)" ]]; then
        echo "Release tag $expected_tag does not resolve to HEAD." >&2
        exit 1
    fi
}

for argument in "$@"; do
    case "$argument" in
        --dry-run | --no-attestations | -q | --quiet | -v | --verbose) ;;
        --index=* | --username=* | --password=* | --token=* | --trusted-publishing=* | \
            --keyring-provider=* | --publish-url=* | --check-url=*) ;;
        *)
            echo "Unsupported publish argument: $argument" >&2
            echo "Use option=value syntax; artifact paths are selected by this script." >&2
            exit 1
            ;;
    esac
done

require_clean_worktree

project_version="$(uv --color never version --short --locked)"
readonly project_version
readonly expected_tag="v${project_version}"
assert_release_tag

run_args=(run --locked)

run_locked() {
    uv "${run_args[@]}" "$@"
}

uv sync --locked --all-groups --all-extras

run_locked pytest
run_locked ruff check src tests
run_locked basedpyright src
run_locked basedpyright --verifytypes nshconfig --ignoreexternal
run_locked sphinx-build -E -a -W --keep-going -b html docs/source docs/build/html
run_locked nox --error-on-missing-interpreters -s tests

uv build --clear

wheels=(dist/*.whl)
source_distributions=(dist/*.tar.gz)
if [[ ${#wheels[@]} -ne 1 || ! -f "${wheels[0]}" ]]; then
    echo "Expected exactly one wheel in dist/." >&2
    exit 1
fi
if [[ ${#source_distributions[@]} -ne 1 || ! -f "${source_distributions[0]}" ]]; then
    echo "Expected exactly one source distribution in dist/." >&2
    exit 1
fi

readonly expected_wheel="dist/nshconfig-${project_version}-py3-none-any.whl"
readonly expected_source_distribution="dist/nshconfig-${project_version}.tar.gz"
if [[ "${wheels[0]}" != "$expected_wheel" ]]; then
    echo "Unexpected wheel name: ${wheels[0]}" >&2
    exit 1
fi
if [[ "${source_distributions[0]}" != "$expected_source_distribution" ]]; then
    echo "Unexpected source distribution name: ${source_distributions[0]}" >&2
    exit 1
fi

readonly package_smoke='import importlib.metadata
import json
import os

import nshconfig as C

expected_version = os.environ["EXPECTED_VERSION"]
assert importlib.metadata.version("nshconfig") == expected_version
assert C.__version__ == expected_version

class Smoke(C.Config):
    value: int
    doubled: int = C.interp(lambda context: context.current(Smoke).value * 2)

work = C.draft(Smoke)
work.value = 21
final = C.finalize(work)
assert final.doubled == 42
value_fingerprint = C.fingerprint(final)
run_record = C.record(final)
assert run_record.fingerprint == value_fingerprint
restored = C.load_record(Smoke, json.loads(run_record.to_json()))
assert restored == final
assert C.fingerprint(restored) == value_fingerprint'

for artifact in "${wheels[0]}" "${source_distributions[0]}"; do
    EXPECTED_VERSION="$project_version" uv run --isolated --no-project \
        --with "$artifact" -- python -c "$package_smoke"
done

# The checks and build can take long enough for an accidental edit to happen.
# Recheck both release invariants immediately before selecting the upload files.
require_clean_worktree
assert_release_tag

if [[ -f "$repo_root/.env" ]]; then
    uv run --no-project --env-file "$repo_root/.env" -- \
        env -u FORCE_COLOR NO_COLOR=1 "$(command -v uv)" publish "$@" \
        "${wheels[0]}" "${source_distributions[0]}"
else
    uv publish "$@" "${wheels[0]}" "${source_distributions[0]}"
fi
