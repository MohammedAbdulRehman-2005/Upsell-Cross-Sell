"""CLI entrypoint to verify the seeded PostgreSQL product catalogue and 420 mappings."""
from __future__ import annotations

import json
import sys

from app.core.logging import configure_logging
from app.db.session import SessionLocal
from app.services.catalogue_loader import verify_database_catalogue


def main() -> int:
    configure_logging()
    try:
        with SessionLocal() as session:
            report = verify_database_catalogue(session)
            print(json.dumps(report.to_dict(), indent=2))
            return 0 if report.status == "PASS" else 1
    except Exception as exc:
        print(
            json.dumps({"status": "ERROR", "error": str(exc)}, indent=2),
            file=sys.stderr,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
