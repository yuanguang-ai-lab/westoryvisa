# Production public snapshot: 2026-09-20

Source: `https://westoryvisa.com`

GitHub comparison baseline:
`2e985e95a1030e497e502c273e954bfd13154d2b`

This directory contains every file under `frontend/` at the baseline plus two
additional production-referenced files (`site-country.css` and
`site-country.js`) as they were publicly served. The HTTP candidate set was
derived from the repository frontend tree; it is not a filesystem listing of
the server.

Summary:

- 39 public files captured with HTTP 200;
- 23 files are byte-for-byte equal to baseline `frontend/` blobs;
- 14 baseline paths contain production-only content not found in Git history;
- 2 paths exist only in production;
- production backend reports API revision 22; baseline source reports revision
  20.

See `frontend-manifest.tsv` for byte counts, SHA-256 checksums, Git blob IDs, and
match classification. Files under `public-frontend/` are preserved for audit
and reconciliation only. This is not a complete production source snapshot and
must not be deployed as-is.
