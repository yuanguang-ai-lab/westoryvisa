# DocFlow Agent source versions

This repository keeps the two original Agent implementations side by side:

- `standalone-agent/` — Agent V1, including document recognition, workflow orchestration, validation, safety controls, provider adapters, storage, and tests.
- `standalone-agent-v2/` — Agent V2 Computer Use execution layer. V2 reuses V1 domain models and is designed to coexist with V1.

Only source code, documentation, examples, and tests are tracked. Local credentials, `.env` files, virtual environments, customer/runtime data, databases, logs, caches, and generated package metadata are intentionally excluded from GitHub.

Each directory contains its own README and `pyproject.toml` with setup and test instructions.
