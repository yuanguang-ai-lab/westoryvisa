# Read-only production source export

The missing backend revision must be obtained from the server before production
can be reconstructed. The expected deployment path from historical project
documentation is `/opt/docflow`; verify it rather than assuming it.

Collect only source and deployment identity. Do not copy `.env` files, Docker
volumes, databases, uploads, customer documents, logs, browser profiles, or
credentials into this public repository.

## Read-only inventory

Run on the server and save the output outside the repository:

```bash
date -u
hostname
docker compose -f /opt/docflow/deploy/docker-compose.yml \
  -f /opt/docflow/deploy/docker-compose.production.yml ps
docker inspect --format '{{.Name}} {{.Image}} {{.Config.Image}}' \
  $(docker compose -f /opt/docflow/deploy/docker-compose.yml \
    -f /opt/docflow/deploy/docker-compose.production.yml ps -q)
git -C /opt/docflow status --short --branch
git -C /opt/docflow rev-parse HEAD
git -C /opt/docflow remote -v
```

If `/opt/docflow` is not a Git checkout, record that fact and generate a source
manifest without reading excluded runtime locations:

```bash
find /opt/docflow -type f \
  -not -path '/opt/docflow/.git/*' \
  -not -path '/opt/docflow/data/*' \
  -not -path '/opt/docflow/deploy/*.env' \
  -not -name '.env' \
  -not -name '*.sqlite*' \
  -not -name '*.db' \
  -not -name '*.log' \
  -print0 | sort -z | xargs -0 sha256sum
```

## Safe source archive

After reviewing the inventory, create an archive that excludes private and
runtime material:

```bash
tar -C /opt/docflow -czf /tmp/westoryvisa-source-only.tgz \
  --exclude='.git' \
  --exclude='.env' \
  --exclude='*.env' \
  --exclude='data' \
  --exclude='*.sqlite*' \
  --exclude='*.db' \
  --exclude='*.log' \
  --exclude='uploads' \
  --exclude='browser-use' \
  --exclude='browser-harness' \
  .
```

Inspect the archive listing before transfer. Import it only into the
`work/reconcile-production-20260920` branch, perform a secret/runtime-data scan,
and then compare it with both the public snapshot and GitHub history.
