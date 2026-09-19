# Production snapshots

This directory contains immutable, non-secret evidence captured from running
environments.

Snapshots must never contain environment files, credentials, customer
documents, databases, logs, browser profiles, session tokens, or other runtime
data. A public HTTP snapshot is not proof of the corresponding backend source
and must not be treated as a deployable release.
