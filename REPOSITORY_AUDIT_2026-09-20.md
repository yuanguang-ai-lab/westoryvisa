# WestoryVisa repository and production audit

Audit date: 2026-09-20 (Asia/Shanghai)

GitHub baseline: `main` at `2e985e95a1030e497e502c273e954bfd13154d2b`

Production endpoint: `https://westoryvisa.com` (`45.76.169.220` at audit time)

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

## Production and security findings

### High: global analytics are available to any authenticated user

`/api/product/analytics` and its session-detail route use `require_user()`
instead of `require_platform_admin()`. The data is global rather than scoped by
organization, so a normal customer account can read site-wide traffic data.

### High: production identity and account controls are incomplete

The public health endpoint reports registration verification mode `none`, an
unconfigured external email service, and an unconfigured payment provider.
This may be acceptable for a private test environment, but it is not a safe
production account lifecycle for a public site.

### Medium: operational details are publicly exposed

`/api/health` and `/backend-status` disclose the API version, worker count,
browser runtime types, translation configuration state, registration mode, and
payment readiness. These endpoints should provide a minimal anonymous liveness
response; detailed readiness should require administrator authentication or
internal network access.

### Medium: build-only files are publicly served

The frontend image copies the entire `frontend/` directory. Production serves
Python helper files, README material, promo rendering sources, and full rendered
videos. No credential file or backend source was found at the probed paths, but
the unnecessary files increase disclosure and attack surface.

### Medium: missing browser security headers

The sampled static response did not include HSTS, Content-Security-Policy,
frame-ancestor/X-Frame-Options, Referrer-Policy, or Permissions-Policy headers.
The Nginx configuration adds `X-Content-Type-Options` only for the generic
location and does not define a consistent security-header policy.

### Medium: authentication has no general login throttling

Password hashing and session-token storage are reasonable for the current
stdlib implementation, and cookies are configured HttpOnly/SameSite/Secure in
production. However, login and registration have no general rate limiter or
account lockout. Origin checking is not a substitute for brute-force control.

## Recommended repair order

1. Obtain a read-only export of `/opt/docflow` and the running container/image
   identities without exporting secrets, customer data, databases, or uploads.
2. Match or archive the production revision-22 backend source and automation
   service source.
3. Reconcile the recovered public frontend into the work branch and make the
   complete application pass tests.
4. Fix analytics authorization and reduce anonymous health/status output.
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
