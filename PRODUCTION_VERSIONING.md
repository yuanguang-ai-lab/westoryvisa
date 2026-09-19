# WestoryVisa production versioning

This repository previously had no reliable link between a Git commit and the
files running on `westoryvisa.com`. Use the following model until deployment is
automated.

## Branch roles

- `main`: reviewed, releasable source. Never edit it directly on a server.
- `work/*`: development and reconciliation work. Merge through a pull request.
- `release/*`: optional stabilization branch when a release needs its own test
  cycle.
- `archive/*`: immutable evidence captured from an existing environment. Never
  deploy from an archive branch unless the archive explicitly says it is a
  complete, verified source snapshot.

The current evidence snapshot is on
`archive/production-public-20260920`. The follow-up reconciliation branch is
`work/reconcile-production-20260920`.

## Required release identity

Every production deployment must record all of the following:

1. full Git commit SHA;
2. source branch and release tag;
3. build timestamp in UTC;
4. frontend and backend image digests;
5. database migration level;
6. the operator and deployment method;
7. post-deploy health-check result;
8. rollback commit or image digest.

Recommended release tags use `production-YYYYMMDD-HHMM` and must point to the
exact commit used to build the images. Do not retag or rebuild an existing
release tag.

## Deployment rule

Production must be built from a clean checkout. Before building:

```bash
git status --porcelain
git rev-parse HEAD
git describe --always --dirty
```

The first command must print nothing and `git describe` must not end in
`-dirty`. Build both containers from the same commit, store their immutable
digests, deploy those digests, then verify `/`, `/api/health`, authentication,
and one non-destructive application workflow.

Never copy individual source files into `/opt/docflow`, edit code inside a
running container, or use a mutable `latest` image as the only rollback point.

## Pull-request policy

Before merging into `main`:

- all automated tests must pass in CI;
- the production snapshot comparison must show only intentional changes;
- database changes must include a forward migration and rollback notes;
- secrets, runtime databases, uploads, logs, and browser profiles must remain
  outside Git;
- at least one reviewer must approve the pull request.

Protect `main` after the current failing tests have been repaired. Require a
pull request, one approval, passing CI, and no force pushes.
