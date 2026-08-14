# GitHub repository scope

This public repository contains the source code and project assets for WestoryVisa / DocFlow.

Included:

- frontend and backend source code
- route pages, styles, scripts, tests, deployment files, and documentation
- browser-extension source, promotional source, rendered MP4/WebM videos, and release packages
- example environment files containing placeholders only

Intentionally excluded from this public repository:

- `data/` (runtime databases, uploaded customer documents, sessions, and generated data)
- real `.env` files and deployment credentials
- private keys, local virtual environments, dependency caches, logs, and build output
- re-creatable promotional-video PNG frame cache (`frontend/promo/rendered/frames/`)

The excluded runtime data is preserved separately and must never be committed because it may contain sensitive customer or credential data.
