# WestoryVisa repository and production audit

Audit date: 2026-09-20 (Asia/Shanghai)

GitHub baseline: `main` at `2e985e95a1030e497e502c273e954bfd13154d2b`

Production endpoint: `https://westoryvisa.com`

## Executive conclusion

Production cannot be mapped to any Git commit. It is a mixed deployment made
from GitHub `main` plus changes applied outside Git.

- The public frontend exposes 39 files derived from the repository frontend.
- 23 files exactly match the baseline commit.
- 14 existing files differ from every blob in all fetched Git branches and
  history.
- 2 production files, `site-country.css` and `site-country.js`, do not exist in
  Git at all.
- Production reports API revision 22 and version
  `2026-08-17-gemini-v2-linux-visible-v1`; GitHub `main` reports revision 20
  and version `2026-08-13-billing-v1`.
- The production backend source is not recoverable through public HTTP and was
  not available through an SSH connection during this audit. It must be
  exported read-only from the server before the production source can be
  considered complete.

The public frontend evidence is preserved under
`production-snapshots/2026-09-20/`. It is evidence, not a deployable release.

## Version-control findings

### Critical: production has no commit-based rollback point

GitHub has no deployments, environments, Actions workflows, or release tags.
`main` is not branch-protected. The server files were changed after the last
GitHub commit: the live homepage reported a 2026-08-31 last-modified timestamp,
while `main` last changed on 2026-08-26.

Impact: a rebuild from GitHub would silently remove production-only frontend
features and would install an older backend API.

### High: duplicate implementations have diverged

The repository contains both legacy root-level source and the intended
`frontend/` and `backend/` boundaries.

- Of 12 duplicated frontend files checked, 9 differ.
- All 12 duplicated backend modules checked differ.
- `server.py` remains as a legacy entry point while production documentation
  says `backend.main` is authoritative.

There is no safe way to know which copy should be edited without first choosing
one canonical implementation and deleting or mechanically generating the
compatibility copy.

### High: the default branch is already red

The main Python suite ran 198 tests: 10 failed and 2 errored. The two errors
include one missing local Browser Use dependency and one invalid task-field
source; the 10 failures are repository consistency and stale-expectation
failures. The standalone practice-lab suite passed all 24 tests.

Do not enable required CI on `main` until these failures are repaired on the
reconciliation branch, otherwise every pull request will be permanently
blocked.

### Medium: stale and oversized pull request

Draft pull request #1 is conflicting, adds roughly 65,000 lines, and mixes a
frontend rewrite with two standalone Agent implementations. It is not a usable
release candidate and should be split or closed after its unique Agent source
has been evaluated.

### Medium: repository bloat

The repository stores rendered MP4/WebM files and release tarballs directly in
Git. The fetched pack is about 24 MiB even though there are only 34 commits.
Future large media should use Git LFS or a release/object store.

## Production and security follow-up

The private audit identified access-control, account-lifecycle, information-
exposure, static-image scope, browser-header, and authentication-hardening work.
Exact security findings are intentionally not published in this public
repository. Complete that review on the reconciliation branch before treating
the service as production-ready.

## Recommended repair order

1. Obtain a read-only export of `/opt/docflow` and the running container/image
   identities without exporting secrets, customer data, databases, or uploads.
2. Match or archive the production revision-22 backend source and automation
   service source.
3. Reconcile the recovered public frontend into the work branch and make the
   complete application pass tests.
4. Resolve the private security-review findings before the next release.
5. Make `frontend/` and `backend/` the only canonical implementations; remove
   or generate legacy root copies.
6. Add CI, release metadata, immutable image tags, and a verified rollback
   procedure.
7. Protect `main`, then require reviewed pull requests and passing checks.
8. Replace manual server edits with a single scripted deployment path.

## Evidence limitations

The snapshot proves only what was publicly served on the audit date. It does
not prove the full contents of the server filesystem, running container layers,
private environment variables, customer-data volumes, or the separate central
automation service. Those items require authorized server access.
