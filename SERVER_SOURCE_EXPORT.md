# Production source acquisition

Collect source from the actual running services, then compare it with the host
checkout. A host directory or image tag alone does not identify running code.

## Observed scope

The authenticated console shows separate frontend, backend, scheduler, OCR
worker, three Agent containers, Caddy, PostgreSQL and a PostgreSQL backup
container. Application source is inside the running containers; `/app/data`
is backed by data volumes and is excluded from source acquisition.

The host `/opt/docflow` directory is not a Git checkout. Agent `/app/runtime`
contains runtime logs and is excluded from source acquisition.

## Read-only inventory

Record container IDs, image IDs/digests, creation/start times, work directories,
mount mappings and Compose file paths. Record environment variable names only.
Do not save full `docker inspect`, environment values, application logs, customer
records or credentials. Record database image identity, not database contents.

## Source selection

Use an explicit reviewed list of source roots and files for each component.
Traverse only those code roots and include regular source/assets files. Do not
follow symlinks. Exclude secrets, every `.env*` variant, private keys, database
files, uploads, browser state, tasks/results, dependency environments, backups
and logs. Save omitted paths and collection errors in the manifest.

Do not archive all of `/opt/docflow` using an exclusion list: an unknown runtime
directory or credential filename could otherwise be copied accidentally.
Collect host build/deployment source separately from deployed container source
and compare the two origins explicitly.

## Evidence and transfer

Create private temporary evidence files, with container origin and file hashes.
Verify service/container identity again after collection. A running process may
have loaded older bytes than its current filesystem; record that limitation.

Transfer the archive over an authenticated encrypted channel, inspect its paths
and scan its contents locally before adding any recovered source to this public
repository. Private configuration values and runtime data must never be added.

## Branch handoff

Keep the production evidence linked to an immutable commit and hash manifest.
Reconcile verified source into `work/reconcile-production-20260920`, with an
explicit mapping from each running component to its repository directory.
The existing public HTTP snapshot is incomplete and cannot be deployed as-is.
