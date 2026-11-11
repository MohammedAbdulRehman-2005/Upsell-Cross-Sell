"""CLI entrypoint to seed the product catalogue and 420 ranked mappings into PostgreSQL."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from app.core.config import get_settings
from app.core.logging import configure_logging
from app.db.session import SessionLocal
from app.services.catalogue_loader import (
    CatalogueDataValidationError,
    CatalogueLoaderService,
    verify_database_catalogue,
    verify_source_files_consistency,
)


def build_parser() -> argparse.ArgumentParser:
    settings = get_settings()
    parser = argparse.ArgumentParser(
        description=(
            "Validate and seed the 70 workbook products and 420 ranked mappings "
            "into PostgreSQL idempotently."
        )
    )
    parser.add_argument(
        "--workbook",
        type=Path,
        default=settings.raw_workbook_path,
        help="Path to authoritative raw source Excel workbook.",
    )
    parser.add_argument(
        "--normalized-json",
        type=Path,
        default=settings.normalized_mappings_path,
        help="Path to normalized product_mappings.json.",
    )
    parser.add_argument(
        "--normalized-csv",
        type=Path,
        default=settings.normalized_mappings_csv_path,
        help="Path to normalized product_mappings.csv.",
    )
    parser.add_argument(
        "--validation-report",
        type=Path,
        default=settings.validation_report_path,
        help="Path to Stage 1 validation_report.json.",
    )
    parser.add_argument(
        "--apply-synthetic-fixture",
        action="store_true",
        help=(
            "Explicitly apply synthetic demonstration catalogue metadata from "
            "data/fixtures/synthetic_catalogue_metadata.json (without overwriting "
            "any VERIFIED_CATALOGUE records)."
        ),
    )
    parser.add_argument(
        "--synthetic-fixture",
        type=Path,
        default=settings.synthetic_catalogue_fixture_path,
        help="Path to synthetic catalogue metadata fixture JSON.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate source files and simulate database load, then roll back without committing.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    logger = configure_logging()

    try:
        records = verify_source_files_consistency(
            workbook_path=args.workbook,
            normalized_json_path=args.normalized_json,
            normalized_csv_path=args.normalized_csv,
            validation_report_path=args.validation_report,
        )
        expected_codes = {r.product_code for r in records}

        with SessionLocal() as session:
            loader = CatalogueLoaderService(session)
            summary = loader.load_from_files(
                workbook_path=args.workbook,
                normalized_json_path=args.normalized_json,
                normalized_csv_path=args.normalized_csv,
                validation_report_path=args.validation_report,
                synthetic_fixture_path=args.synthetic_fixture,
                apply_synthetic_metadata=args.apply_synthetic_fixture,
                dry_run=args.dry_run,
            )

            output: dict[str, object] = {
                "status": "DRY_RUN_PASS" if args.dry_run else "SEED_PASS",
                "load_summary": summary.to_dict(),
            }

            if not args.dry_run:
                verification = verify_database_catalogue(
                    session,
                    expected_workbook_codes=expected_codes,
                )
                output["database_verification"] = verification.to_dict()
                if verification.status != "PASS":
                    print(json.dumps(output, indent=2), file=sys.stderr)
                    return 1

            print(json.dumps(output, indent=2))
            return 0

    except CatalogueDataValidationError as exc:
        logger.error("Catalogue validation failed: %s", exc)
        error_payload = {
            "status": "VALIDATION_FAILED",
            "errors": exc.errors,
        }
        print(json.dumps(error_payload, indent=2), file=sys.stderr)
        return 1
    except Exception as exc:
        logger.exception("Unexpected error during catalogue seeding")
        error_payload = {
            "status": "ERROR",
            "error": str(exc),
        }
        print(json.dumps(error_payload, indent=2), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
