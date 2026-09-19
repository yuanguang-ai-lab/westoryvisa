#!/usr/bin/env python3
"""Verify this fixed source snapshot without importing or running application code."""
import hashlib
import json
from pathlib import Path, PurePosixPath


def verify(root):
    manifest = json.loads((root / 'PROVENANCE.json').read_text(encoding='utf-8'))
    expected = set()
    for record in manifest['files']:
        relative = PurePosixPath(record['path'])
        if relative.is_absolute() or '..' in relative.parts or str(relative) != record['path']:
            raise ValueError('Unsafe manifest path')
        if record['path'] in expected:
            raise ValueError('Duplicate manifest path')
        expected.add(record['path'])
        path = root / relative
        if path.is_symlink() or not path.is_file():
            raise ValueError(f'Missing or linked file: {relative}')
        payload = path.read_bytes()
        if len(payload) != record['bytes'] or hashlib.sha256(payload).hexdigest() != record['sha256']:
            raise ValueError(f'Baseline bytes changed: {relative}')
    actual = {str(p.relative_to(root)) for namespace in ('components', 'build-evidence')
              for p in (root / namespace).rglob('*') if p.is_file() or p.is_symlink()}
    if actual != expected:
        raise ValueError('Source tree and manifest membership differ')
    return len(expected)


if __name__ == '__main__':
    count = verify(Path(__file__).resolve().parents[1])
    print(f'OK: {count} source files exactly match the captured baseline.')
