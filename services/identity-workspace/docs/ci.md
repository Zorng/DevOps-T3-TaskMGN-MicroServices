# Identity & Workspace Continuous Integration

The [Identity workflow](../../../.github/workflows/identity-workspace.yml) installs locked dependencies, runs separate test suites, builds and smoke-tests the service image, and gates GitHub Container Registry publishing. The workflow is implemented and locally validated. A hosted GitHub Actions result and actual registry publication remain unverified until the changes are pushed and the corresponding events run.

## Events and required checks

| Event | Tests and container verification | Publish image |
| --- | --- | --- |
| Open/update a pull request | Yes | No |
| Push to `main` | Yes | After every required Identity check succeeds |
| Merge queue check | Yes | No |
| Manual workflow dispatch | Yes | No |
| Push a feature branch without an open PR | No push-triggered run | No |

There are no path filters: shared `api_contracts/` changes and unrelated repository changes still run the Identity checks. This matches the existing repository workflows and avoids a skipped path-filtered job appearing to satisfy service verification.

Configure **Identity required checks** as a required status check for `main` after its first hosted run. It always evaluates the test matrix and container job, failing if either result is failed, cancelled, or skipped. Existing contract/Task checks and the repository's review policy remain applicable. Branch protection and required reviews are separate administrator settings; adding YAML does not configure them.

Pull-request runs for the same ref cancel older runs. Main runs are serialized rather than cancelling an in-progress publication.

## Jobs

| Job | Behavior |
| --- | --- |
| `tests` | Independent unit/request/integration matrix entries, with fail-fast disabled so each suite reports its result |
| `container` | Requires the entire test matrix; builds only the Identity context and smoke-tests that image |
| `required` | Always checks that the matrix and container job both succeeded; stable required-check name |
| `publish` | Only for successful pushes to `main`; requires the aggregate gate and reuses the image that passed smoke verification |

Test jobs use Ubuntu 24.04, Python 3.13.16 from [.python-version](../.python-version), uv 0.13.8, and `uv sync --locked`. GitHub actions are pinned to immutable commits. The container uses the separately digest-pinned Python/uv images documented in the [container guide](container.md). No developer environment file or database password is uploaded to GitHub.

Each matrix entry runs the equivalent of these commands from the service directory, with `unit`, `request`, or `integration` as the suite:

```text
uv sync --locked
uv pip check
uv run --locked pytest tests/<suite> -q --junitxml=test-results/<suite>.xml
python scripts/verify_test_report.py test-results/<suite>.xml
```

Pytest returns nonzero for failures and an empty collection. Bash error handling stops the step immediately. The [JUnit guard](../scripts/verify_test_report.py) additionally rejects missing/invalid reports, zero test cases, failures, errors, and skips, including suites where pytest otherwise succeeds after skipping all tests. It does not hardcode the current test count. No `continue-on-error` or empty-suite success flag is used.

Integration tests create their own PostgreSQL 18.7 instance with disposable credentials, migrations, and cleanup. They fail if Docker is unavailable. The container job uses the standard-library smoke script, which creates another isolated Compose project and verifies real HTTP behavior and database outage/recovery. See [testing](testing.md) and [container verification](container.md#automated-smoke-test).

## Reports and tested image transfer

Suite reports are uploaded even if tests fail, under `identity-unit-tests`, `identity-request-tests`, and `identity-integration-tests`. `identity-container-smoke` contains the JSON smoke result. Missing reports fail their upload step. Reports are retained for seven days and replaced on a rerun of the same workflow.

After successful main-branch container verification, the image is saved as a compressed Docker archive with a SHA-256 checksum. `identity-tested-image` is retained for one day. The publishing job downloads that artifact, verifies the checksum, and loads the image. It does not rebuild a different image before publishing. Pull-request/manual runs do not create the image-transfer artifact.

## Registry publishing

Successful `main` pushes publish these lowercase GHCR tags:

```text
ghcr.io/<owner>/<repository>/identity-workspace:sha-<full-commit-sha>
ghcr.io/<owner>/<repository>/identity-workspace:main
```

The commit tag identifies the source revision; `main` tracks the latest successful main publication. OCI source/revision labels associate the image with the repository and commit. No deployment is performed.

Only the publishing job receives `packages: write`; tests and image verification use read-only repository permissions. Authentication uses GitHub's automatic `GITHUB_TOKEN` through Docker's password-stdin input, followed by logout. No PAT or registry password belongs in source control. Repository/organization package policy must permit publication; an existing package must grant this repository's Actions access. Package visibility/access is managed in GitHub Packages separately.

References: [GitHub Docker publishing](https://docs.github.com/en/actions/tutorials/publish-packages/publish-docker-images), [workflow dependency/permission syntax](https://docs.github.com/en/actions/reference/workflows-and-actions/workflow-syntax), and [uv GitHub Actions integration](https://docs.astral.sh/uv/guides/integration/github/).

## Get the first hosted result

Review the scaffold and workflow, then commit/push the branch when ready. From the repository root:

```powershell
git add .github/workflows/identity-workspace.yml services/identity-workspace
git diff --cached --stat
git diff --cached --name-only
git commit -m "feat(identity-workspace): scaffold FastAPI service and test pipeline"
git push -u origin feat/identity-workspace/fastapi-scaffold
```

Real environment files, password files, tools, caches, and reports remain ignored. Open a pull request from this branch into `main`; pushing this feature branch alone does not match the workflow's push trigger. In the PR checks or GitHub Actions tab, inspect **Identity and Workspace**, confirm the three test entries and container/aggregate checks succeed, and inspect their artifacts. Publishing should be skipped for the PR.

After the repository's review/check requirements are met and the PR is merged, the main push validates again and then publishes. Obtain the actual hosted run URL and result before claiming the task's passing-pipeline criterion is complete.

## Local verification recorded for Step 12

Verified on 2026-11-15:

- actionlint 1.7.12 accepted the workflow; its local download was checksum-verified. Official release references were checked for all action commit pins, and the Python 3.13.16 Ubuntu 24.04 runner build was confirmed available.
- Unit suite: 152 passed on Windows and in an isolated Linux environment. Request suite: 80 passed in the Linux check. PostgreSQL integration suite: 27 passed against a disposable real database. All 259 service tests passed with zero failures, errors, or skips.
- All three suite reports passed the JUnit guard. Real intentionally failing/empty pytest invocations returned exit codes 1/5, and the guard CLI rejected their unsuccessful/empty report examples.
- The actual aggregate gate's Bash script accepted only success/success across all 16 combinations of success/failure/cancelled/skipped test and container results.
- The CI-tagged image built and passed the isolated container smoke test. Its archive was checksummed and loaded locally with identical image configuration/filesystem layers; no registry login/push was performed.

The Linux unit/request check used the pinned Python/uv container environment; database integration was driven from the local Windows host. These checks do not emulate hosted GitHub action execution, package permissions, artifact-service transfer, or a hosted Ubuntu integration run. No remote workflow run is claimed. The original service README is retained.

Related documents: [scaffold scope](scaffold-scope.md), [stack decisions](stack-decisions.md), [test guide](testing.md), and [container guide](container.md).
