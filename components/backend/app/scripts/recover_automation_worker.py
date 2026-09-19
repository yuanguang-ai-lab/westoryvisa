#!/usr/bin/env python3
"""Clear a quarantined worker only after its container/browser was reset."""

import argparse
import os
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("worker_id")
    parser.add_argument(
        "--confirm-container-reset",
        action="store_true",
        help="required after force-recreating the exact worker container",
    )
    args = parser.parse_args()
    if not args.confirm_container_reset:
        raise SystemExit(
            "Refused: recreate the exact worker container, then pass "
            "--confirm-container-reset"
        )
    if not os.environ.get("DOCFLOW_DATABASE_URL", "").strip():
        raise SystemExit("DOCFLOW_DATABASE_URL is required")
    from backend.application import connect, init_db
    from backend.automation_queue import clear_worker_quarantine

    init_db()
    clear_worker_quarantine(connect, worker_id=args.worker_id)
    print(f"Worker quarantine cleared: {args.worker_id}")


if __name__ == "__main__":
    main()
