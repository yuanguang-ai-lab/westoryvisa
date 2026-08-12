# GitHub backup scope

This private repository is a source and project-asset backup of WestoryVisa / DocFlow.

Included:

- frontend and backend source code
- route pages, styles, scripts, tests, deployment files, and documentation
- browser-extension source, promotional source, rendered MP4/WebM videos, and release packages
- example environment files containing placeholders only

Intentionally excluded from GitHub:

- `data/` (runtime databases, uploaded customer documents, sessions, and generated data)
- real `.env` files and deployment credentials
- private keys, local virtual environments, dependency caches, logs, and build output
- re-creatable promotional-video PNG frame cache (`frontend/promo/rendered/frames/`)

The excluded runtime data is preserved in the separate local full-project archive. Keep that archive and its checksum in secure storage; it may contain sensitive customer and credential data.
