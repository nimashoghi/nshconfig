# Publishing releases

Releases use the manually dispatched `.github/workflows/publish.yml` workflow on `main`. It requires successful CI for the same commit, verifies the requested version against `pyproject.toml`, builds distributions, and runs the Protenix example from isolated wheel and source-distribution installs. A separate job downloads those exact artifacts and publishes them through PyPI Trusted Publishing.

## One-time setup

In [the project's PyPI publishing settings](https://pypi.org/manage/project/nshconfig/settings/publishing/), register a GitHub publisher with these exact values:

| Field | Value |
| --- | --- |
| Owner | `nimashoghi` |
| Repository | `nshconfig` |
| Workflow filename | `publish.yml` |
| Environment | `pypi` |

The GitHub `pypi` environment allows deployments from the `main` branch. Only the upload job has `id-token: write`; no long-lived PyPI token is stored in GitHub. The workflow filename and environment must match the registration on PyPI. See [PyPI's setup documentation](https://docs.pypi.org/trusted-publishers/adding-a-publisher/).

## Release procedure

1. Commit the intended package version and lockfile, review the PR, and merge it to `main`.
2. Wait for the main-branch CI run to pass. Local release validation remains available through `./scripts/publish.sh --dry-run --trusted-publishing never`.
3. Dispatch the workflow with the exact version:

   ```bash
   gh workflow run publish.yml --repo nimashoghi/nshconfig --ref main -f version=3.0.0a0
   ```

4. Check the workflow result and verify the release on PyPI. The `dist` workflow artifact contains the distributions that were uploaded.
5. Create the corresponding version tag/GitHub release at that workflow's source commit, using the uploaded distributions as release assets. Mark alpha/beta/rc versions as prereleases.

Dispatching from another branch skips publication. A failed CI/version check prevents the upload job from running. The workflow fails explicitly if the PyPI publisher is missing or mismatched. A new release requires a new version; published files cannot be overwritten.
