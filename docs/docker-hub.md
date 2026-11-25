# Docker Hub image

The published codx-junior image is the **only supported release artifact**: an
unattended install pulls it instead of compiling the product on the customer's
machine. This page documents what is published, which tag to use, and the git
flow that produces it.

::: tip Source of truth
The image is defined by code, not by this page. When they disagree, the code
wins — and this page is the bug.

| Concern | File |
|---|---|
| Registry, tags, build args, smoke test | [`.github/workflows/release.yml`](https://github.com/codx-junior/codx-junior/blob/main/.github/workflows/release.yml) |
| Docker Hub listing (description and README) | [`DOCKERHUB.md`](https://github.com/codx-junior/codx-junior/blob/main/DOCKERHUB.md), synced by the workflow |
| Manual / multi-arch publish | [`scripts/dockerhub_publish.sh`](https://github.com/codx-junior/codx-junior/blob/main/scripts/dockerhub_publish.sh) |
| Image contents | [`codx-junior-installer/demo/Dockerfile`](https://github.com/codx-junior/codx-junior/blob/main/codx-junior-installer/demo/Dockerfile) |
| Ref → image tag mapping | [`codx-junior-installer/install.sh`](https://github.com/codx-junior/codx-junior/blob/main/codx-junior-installer/install.sh) |
| Build provenance reported by the API | [`api/codx/junior/version.py`](https://github.com/codx-junior/codx-junior/blob/main/api/codx/junior/version.py) |
| Deployment contract | [`codx-junior-installer/cloud/DEPLOYMENT-CONTRACT.md`](https://github.com/codx-junior/codx-junior/blob/main/codx-junior-installer/cloud/DEPLOYMENT-CONTRACT.md) |
:::

## The Docker Hub page

The listing on [Docker Hub](https://hub.docker.com/r/codxjuniorai/codx-junior)
is not edited in the web UI. Its short description and full README come from
[`DOCKERHUB.md`](https://github.com/codx-junior/codx-junior/blob/main/DOCKERHUB.md)
in this repository. The release workflow's `document` job runs after the image
has been built and smoke-tested (or alone for a `description_only` dispatch),
and PATCHes the Docker Hub API with that file. A change to the listing is
therefore reviewed like any other documentation change and cannot drift from
the image documentation.

To update it immediately (for example after editing `DOCKERHUB.md` without
cutting a release), either run the `release` workflow manually with
`description_only` checked — that skips the image build — or run the script
locally:

```bash
DOCKERHUB_TOKEN=... bash scripts/dockerhub_description.sh --readme DOCKERHUB.md
# dry run, no token needed:
bash scripts/dockerhub_description.sh --dry-run
```

The script validates the short description (100 characters) and full README
(25,000 characters) limits before sending anything. It uses
`DOCKERHUB_NAMESPACE`/`IMAGE_NAME`, or the image coordinate in `IMAGE`.

## Repository and tags

The image lives on Docker Hub at
[`codxjuniorai/codx-junior`](https://hub.docker.com/r/codxjuniorai/codx-junior).

```text
docker.io/codxjuniorai/codx-junior:<tag>
```

| Tag | What it is | Published by | Mutable |
|---|---|---|---|
| `latest` | complete app image built from `main` | release workflow on every push to `main` | **yes** — rolling |
| `vX.Y.Z` | pinned release (`v` prefix, for example a published `v1.2.3`) | release workflow on a `v*.*.*` tag | no — treat as immutable |
| `dind` | Docker-in-Docker wrapper (installer + runtime, not the app) | manual `publish:dind` script | yes |
| `dev` | local development build | `dev-install.sh`, local only | n/a — not a release |

The `vX.Y.Z` tag is the one to pin in customer infrastructure. `latest` is a
moving branch: a broken commit on `main` breaks every new workspace that pulls
it. The installer prints a warning when a non-release ref silently falls back to
`:latest`.

::: warning A git tag is not an image
Only tags that actually appear on the Docker Hub tags page exist as images. A
`git tag` alone publishes nothing: the release workflow has to run successfully
for that tag. Check the tags page (or `dockerhub_tags`) before pinning a version
in an install.
:::

### Architectures

* The **release workflow** builds on the GitHub runner's native platform
  (`linux/amd64`) and does not request a multi-arch manifest.
* The **manual publish script** defaults to
  `PLATFORMS=linux/amd64,linux/arm64` and pushes one manifest list.

Use `docker buildx imagetools inspect` before promising an architecture:

```bash
docker buildx imagetools inspect codxjuniorai/codx-junior:latest
```

### Size

The image is deliberately complete: compiled SPA, API, background worker,
embedded LanceDB, Docker CLI + compose/buildx, Playwright/Chromium, TeX Live,
pandoc, tesseract and ffmpeg. Expect roughly **8–12 GB on disk** after pull.
There is no "slim" flavour; a feature must not fail because the release image
left a dependency out.

## Pull and run

The supported path is the installer, which writes the env file, volumes and
health check for you:

```bash
curl -fsSL https://raw.githubusercontent.com/codx-junior/codx-junior/main/install.sh \
  | sudo bash -s -- --provider deepseek --key sk-xxxxxxxx
```

To pin a release instead of `latest`:

```bash
sudo ./install.sh --ref vX.Y.Z --non-interactive </dev/null
# use a tag that exists on Docker Hub
```

A direct `docker run` is useful for a smoke test, not for production — it skips
the projects/settings mounts:

```bash
docker run --rm -p 19981:19981 \
  -e CODX_JUNIOR_AI_PROVIDER=deepseek \
  -e CODX_JUNIOR_AI_API_KEY=sk-xxxxxxxx \
  codxjuniorai/codx-junior:latest
# -> http://localhost:19981
```

The container publishes **19981**. The API itself listens on 19980 behind
nginx; `/api/health` is served through the published port. Persist
`/home/codx-junior-projects` (projects) and `/home/codx-junior/.codx-junior`
(settings) if you run it by hand; see [Getting started](/getting-started).

## Which image does the installer use?

`install.sh` derives the image from the requested git ref. This is the contract
the rest of the deployment tooling relies on:

| Requested ref | Image |
|---|---|
| `vX.Y.Z` (for example `--ref v1.2.3`) | `codxjuniorai/codx-junior:vX.Y.Z` |
| `main` | `codxjuniorai/codx-junior:latest` |
| any other branch or commit | `codxjuniorai/codx-junior:latest` |
| `--dev` on a host | `codxjuniorai/codx-junior:dev`, built from the checkout |
| DIND mode | wrapper from `codxjuniorai/codx-junior:dind` |
| `--image IMAGE` | exactly what you pass; overrides the mapping |
| `--build` | builds locally from the checkout instead of pulling |

A failed pull is **fatal**. The installer prints the tag, the `--build`
alternative and "nothing was started", then exits non-zero. There is no silent
fallback to a 20-minute compile. See
[Self-hosted or managed](/managed) and the
[deployment contract](https://github.com/codx-junior/codx-junior/blob/main/codx-junior-installer/cloud/DEPLOYMENT-CONTRACT.md).

## Provenance and verification

Every release build bakes three build arguments into the image:

| Build arg | Meaning |
|---|---|
| `CODX_JUNIOR_BUILD_VERSION` | release version (`0.0.1`) or `0.0.0-main.<commit>` |
| `CODX_JUNIOR_BUILD_REF` | git ref (`vX.Y.Z`, `main`) |
| `CODX_JUNIOR_BUILD_COMMIT` | short commit (`12` chars) |

`GET /api/health` reports them, so a deployment can assert that the build it
asked for is the build that answered:

```bash
curl -fsS http://127.0.0.1:19981/api/health
# {"status":"ok","version":"...","ref":"vX.Y.Z","commit":"..."}
```

The release workflow runs the same assertion against the freshly pushed image
before publishing the GitHub release: start the container, poll `/api/health`,
and fail if `ref` or `commit` do not match the tag that was built.

## Git flow: from branch to published image

There is no `develop` branch. `main` is the integration branch, release tags are
cut from it, and a tag is the only thing that produces an immutable image.

```text
feature branch ──PR──▶ main ──push──▶ release workflow ──▶ :latest
                        │
                        └──tag vX.Y.Z + push──▶ release workflow ──▶ :vX.Y.Z
                                                              └──▶ GitHub release
```

### 1. Change on a branch

```bash
git switch -c feat/my-change main
# edit, test
git add -A
git commit -m "describe the change"
git push -u origin feat/my-change
```

Autonomous agents use isolated worktrees on `codx/task/<slug>-<hash>` branches
instead of the main checkout; merge that branch is the human review step. See
[Git and code review](/features/code-review).

The repository ships a CISO pre-commit gate. Enable it once per clone:

```bash
git config core.hooksPath .githooks
```

Do not bypass it with `--no-verify`; the gate is what keeps credentials and
malicious instructions out of commits (and therefore out of images).

### 2. Merge to `main` — rolling `:latest`

A push to `main` triggers **release → publish**:

* builds `codx-junior-installer/demo/Dockerfile` from the repository root;
* tags the image `codxjuniorai/codx-junior:latest`;
* sets `version=0.0.0-main.<commit>`, `ref=main`, `commit=<commit>`;
* asserts the image ships `docker`, `docker compose` and `docker buildx`;
* starts the image and requires `/api/health` to report ref `main` and the same
  commit;
* does **not** create a GitHub release and does **not** move a version tag.

`:latest` is therefore always "some commit on main", never a release. Do not
deploy it to customer infrastructure.

### 3. Tag a release — immutable `:vX.Y.Z`

Tags are created by the repository owner, not by the workflow:

```bash
git switch main
git pull --ff-only
git tag -a v0.0.2 -m "codx-junior v0.0.2"
git push origin v0.0.2
```

The tag push triggers the same workflow with:

* image tag `codxjuniorai/codx-junior:v0.0.2` (the `v` is part of the tag);
* `version=0.0.2`, `ref=v0.0.2`, `commit=<commit>`;
* a GitHub release with generated notes and the `install.sh --ref v0.0.2`
  install snippet.

Rules that keep the pin meaningful:

* Never move or re-push an existing release tag; publish a new patch version.
* If a release run fails, delete the GitHub release and the tag, fix `main`,
  and tag again — do not leave a tag without its image.
* Confirm the run published the image before announcing the release. The
  deploy path does not compile, so a missing image means the install fails.

### 4. Hotfix

Branch from the release tag when `main` already contains unrelated work, or from
`main` when it does not:

```bash
git switch -c hotfix/v0.0.2-cve-1234 v0.0.2
# fix + test
git add -A
git commit -m "fix: ..."
git push -u origin hotfix/v0.0.2-cve-1234
# PR into main, merge, then tag the next patch
git switch main && git pull --ff-only
git tag -a v0.0.3 -m "codx-junior v0.0.3"
git push origin v0.0.3
```

The patched image is `:v0.0.3`. `:v0.0.2` stays exactly as it was, so rollbacks
remain possible.

### 5. The DIND wrapper is published separately

The release workflow only publishes the **app** image. The DIND wrapper has its
own Dockerfile and tag, and is published manually when it changes:

```bash
cd codx-junior-installer
DOCKERHUB_TOKEN=... npm run publish:dind
# -> codxjuniorai/codx-junior:dind
```

The wrapper pulls the inner app image at start time, so a `dind` publish is only
needed when `codx-junior-installer/dind/` or the installer/runtime files it
ships change — not for every app release.

### 6. Manual publish (break-glass)

CI is the normal path. The same script used by the AI tools can publish from a
checkout:

```bash
DOCKERHUB_TOKEN=... IMAGE_TAGS="0.0.2,latest" PLATFORMS="linux/amd64,linux/arm64" \
  ./scripts/dockerhub_publish.sh
```

```bash
# dry run: prints the docker commands, needs no token
DRY_RUN=true ./scripts/dockerhub_publish.sh

# build only, no push
./scripts/dockerhub_publish.sh --no-push
```

Prefer an explicit version tag for a manual publish. Pushing `latest` by hand
moves the floating tag to an unverified commit, outside the workflow's smoke
test and provenance assertion.

The script reads its configuration from the environment (token, namespace,
image name, tags, Dockerfile, context, platforms, push/build toggles). Run
`./scripts/dockerhub_publish.sh --help` for the full list. Credentials are fed
to `docker login --password-stdin` and never appear on the command line.

### 7. Credentials

| Secret | Where | Used by |
|---|---|---|
| `DOCKERHUB_USERNAME` | GitHub repository secret | release workflow login and listing sync |
| `DOCKERHUB_TOKEN` | GitHub repository secret | release workflow login and listing sync |
| `DOCKERHUB_TOKEN` | environment or project secret vault (`Settings → Secrets`) | manual publish, description sync, AI tools |

The token needs read/write access to the `codxjuniorai` organisation. Never
commit it. The AI tools redact it from every response and only report whether a
value is present, missing or rejected.

## Docker Hub management tools

The product exposes five tools for agents and chat (implementation:
`api/codx/junior/tools/dockerhub_tools.py`). They use the same namespace and
token configuration as the script:

| Tool | What it does |
|---|---|
| `dockerhub_whoami` | Reports namespace, user and token status (never the value) |
| `dockerhub_tags` | Lists published tags with date, size, architectures and digest |
| `dockerhub_repositories` | Lists the organisation's repositories with visibility and pulls |
| `dockerhub_publish` | Builds and pushes through `scripts/dockerhub_publish.sh` |
| `dockerhub_delete_tag` | Deletes one tag (requires `confirm`; refuses `latest`/`stable`) |

A publish can take hours on a cold multi-arch cache. `dockerhub_publish`
supports an optional timeout, but omitting it means no limit; the run writes
its log under `.codx/logs/dockerhub_publish-<timestamp>.log` so it can be
followed after the tool call returns.

## Keeping this page true

The image is easy to change and easy to stop documenting. Any change to the
pipeline must update the matching row here, in the same pull request. The
Docker Hub listing itself is source-controlled in `DOCKERHUB.md` and synced by
the release workflow's `document` job.

| If you change… | Also update |
|---|---|
| image name, namespace or tag scheme | this page, `DOCKERHUB.md`, `codx-junior-installer/README.md`, the installer defaults |
| `install.sh` ref → tag mapping | this page, `cloud/DEPLOYMENT-CONTRACT.md`, installer tests |
| Dockerfile path or build context | `release.yml`, `codx-junior-installer/package.json`, this page |
| platforms | this page (`Architectures`) and `PLATFORMS` in the script |
| published tags (add/remove) | this page and the tag table in `DOCKERHUB.md` |
| Docker Hub listing copy | `DOCKERHUB.md`; the `document` job publishes it |
| `dind` wrapper | this page (`DIND` section) and `codx-junior-installer/README.md` |
| token or secret names | this page (`Credentials`) and the workflow comments |

Release checklist:

1. `main` is green and the docs build passes.
2. The image change (if any) is reflected in this page and in `DOCKERHUB.md`.
3. Tag and push; watch the release workflow finish.
4. Verify the published tag: `docker manifest inspect` (or
   `docker buildx imagetools inspect`) and `/api/health` ref/commit.
5. Confirm the workflow created the GitHub release for the tag.
6. Confirm the `document` job updated the Docker Hub page; if it failed, run
   `bash scripts/dockerhub_description.sh` with a token.

## Troubleshooting

| Symptom | Cause / fix |
|---|---|
| `pull access denied` | The tag does not exist, or the image was never published. Check `dockerhub_tags`, or install with `--build`. |
| `manifest unknown` on `vX.Y.Z` | The tag was pushed but the release workflow did not publish (or failed). Check the Actions run for that tag. |
| `exec format error` | The image has no manifest for this architecture. Check `docker buildx imagetools inspect`; CI currently publishes `linux/amd64`. |
| `/api/health` reports the wrong `ref` | A stale container or a different tag is running. Compare `ref`/`commit` with the tag you asked for; recreate the container. |
| A DIND instance keeps an old inner image | `dind` pins nothing: the wrapper pulls `latest` for the inner install. Re-run `install.sh --update`; see [Getting started](/getting-started). |
| `unauthorized: authentication required` | The token is missing, expired or read-only. Run `dockerhub_whoami`, rotate the token, and re-store the secret. |
| The Docker Hub page shows old copy | The `document` job failed or has not run since the edit. Check the Actions run, then run `bash scripts/dockerhub_description.sh` with a token. |

## Related

* [Getting started](/getting-started) — install and update paths.
* [Self-hosted or managed](/managed) — support model.
* [Configuration](/configuration) — environment variables.
* [Git and code review](/features/code-review) — branches, PR views and task
  worktrees.
