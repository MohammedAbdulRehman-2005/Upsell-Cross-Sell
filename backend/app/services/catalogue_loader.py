"""Stage 3 Product Catalogue Seeding & Mapping Data Integrity Service.

Responsibilities:
1. Parse and validate the authoritative Excel workbook (`product_mapping_source.xlsx`).
2. Cross-verify workbook contents against normalized JSON (`product_mappings.json`),
   flat CSV (`product_mappings.csv`), and validation report (`validation_report.json`).
3. Validate product and mapping integrity before touching the database.
4. Load products and mappings idempotently inside a single database transaction:
   - Never infer Rx, stock, active status, or pricing from product names.
   - Keep eligibility metadata unknown (`None`, `metadata_source=WORKBOOK_ONLY`) unless
     an explicit synthetic fixture or verified catalogue source is supplied.
   - Never overwrite existing `VERIFIED_CATALOGUE` metadata with `SYNTHETIC_FIXTURE`
     or `WORKBOOK_ONLY` values.
5. Verify resulting database state via SQL/ORM integrity checks.
"""
from __future__ import annotations

import csv
import hashlib
import json
import re
import xml.etree.ElementTree as ET
import zipfile
from dataclasses import asdict, dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.enums import CatalogueMetadataSource
from app.models.product import Product, ProductMapping
from app.repositories.product_repository import ProductRepository
from app.schemas.product import ProductCreate, ProductMappingCreate, ProductUpdate

EXPECTED_HEADERS: list[str] = [
    "FG Code",
    "Product Name",
    "Together Sold Product 1",
    "FG Code Product 1",
    "Together Sold Product 2",
    "FG Code Product 2",
    "Together Sold Product 3",
    "FG Code Product 3",
    "Together Sold Product 4",
    "FG Code Product 4",
    "Together Sold Product 5",
    "FG Code Product 5",
    "Together Sold Product 6",
    "FG Code Product 6",
]

EXPECTED_SOURCE_SHEET = "Sample Product Mapping"
EXPECTED_RANKS = (1, 2, 3, 4, 5, 6)


class CatalogueDataValidationError(ValueError):
    """Raised when source dataset, cross-file consistency, or mapping integrity fails."""

    def __init__(self, message: str, errors: list[str] | None = None) -> None:
        self.errors = errors or [message]
        formatted = message if not errors else f"{message}:\n- " + "\n- ".join(errors)
        super().__init__(formatted)


@dataclass(frozen=True)
class SourceMappingEntry:
    """Validated mapping entry extracted from the source dataset."""

    source_product_code: str
    recommended_product_code: str
    recommended_product_name: str
    recommendation_rank: int
    source_excel_row: int | None = None


@dataclass(frozen=True)
class SourceProductRecord:
    """Validated source product record extracted from the source dataset."""

    product_code: str
    product_name: str
    source_excel_row: int | None = None
    mappings: tuple[SourceMappingEntry, ...] = ()


@dataclass(frozen=True)
class SyntheticMetadataRecord:
    """Explicitly labelled synthetic demonstration metadata for a product."""

    product_code: str
    is_rx: bool | None
    is_active: bool | None
    available_quantity: int | None
    unit_price: Decimal | None = None
    currency: str = "INR"
    image_url: str | None = None
    product_url: str | None = None


@dataclass
class CatalogueLoadSummary:
    """Detailed audit report of a catalogue and mapping load execution."""

    dry_run: bool = False
    synthetic_fixture_applied: bool = False
    source_products_count: int = 0
    source_mappings_count: int = 0
    products_inserted: int = 0
    products_updated: int = 0
    products_unchanged: int = 0
    products_rejected: int = 0
    verified_metadata_preserved: int = 0
    mappings_inserted: int = 0
    mappings_updated: int = 0
    mappings_unchanged: int = 0
    mappings_rejected: int = 0
    validation_errors: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class DatabaseCatalogueVerificationReport:
    """Actual database integrity metrics queried from PostgreSQL / SQLAlchemy."""

    status: str
    workbook_products_loaded: int
    total_catalogue_products: int
    mapping_records_loaded: int
    distinct_source_products_in_mappings: int
    mappings_per_source_min: int
    mappings_per_source_max: int
    sources_without_exact_6_ranks: list[str]
    invalid_ranks: int
    self_recommendations: int
    duplicate_source_rank_pairs: int
    duplicate_source_target_pairs: int
    missing_source_products: int
    missing_recommended_products: int
    workbook_only_products: int
    synthetic_metadata_products: int
    verified_metadata_products: int
    unknown_eligibility_products: int
    errors: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


_OPENXML_NS = {
    "main": "http://schemas.openxmlformats.org/spreadsheetml/2006/main",
    "rel": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
    "pkgrel": "http://schemas.openxmlformats.org/package/2006/relationships",
}


def _col_letters_to_index(col_letters: str) -> int:
    idx = 0
    for ch in col_letters:
        idx = idx * 26 + (ord(ch.upper()) - ord("A") + 1)
    return idx - 1


def _read_xlsx_sheet_rows(workbook_path: Path, sheet_name: str) -> list[list[Any]]:
    """Read worksheet rows from an `.xlsx` file using openpyxl if available, else stdlib XML."""
    try:
        from openpyxl import load_workbook  # type: ignore[import-not-found]

        workbook = load_workbook(workbook_path, data_only=True)
        if sheet_name not in workbook.sheetnames:
            raise CatalogueDataValidationError(
                f"Required sheet '{sheet_name}' is missing in {workbook_path.name}"
            )
        sheet = workbook[sheet_name]
        return [list(r) for r in sheet.iter_rows(values_only=True)]
    except ModuleNotFoundError:
        with zipfile.ZipFile(workbook_path, "r") as zf:
            wb_root = ET.fromstring(zf.read("xl/workbook.xml"))
            rels_root = ET.fromstring(zf.read("xl/_rels/workbook.xml.rels"))
            rel_target_by_id = {
                rel.attrib["Id"]: rel.attrib["Target"].lstrip("/")
                for rel in rels_root.findall("pkgrel:Relationship", _OPENXML_NS)
            }
            sheet_target: str | None = None
            for sheet_el in wb_root.findall(".//main:sheet", _OPENXML_NS):
                if sheet_el.attrib.get("name") == sheet_name:
                    rel_id = sheet_el.attrib.get(
                        "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id"
                    )
                    if rel_id and rel_id in rel_target_by_id:
                        target = rel_target_by_id[rel_id]
                        sheet_target = target if target.startswith("xl/") else f"xl/{target}"
                    break
            if sheet_target is None:
                raise CatalogueDataValidationError(
                    f"Required sheet '{sheet_name}' is missing in {workbook_path.name}"
                )

            shared_strings: list[str] = []
            if "xl/sharedStrings.xml" in zf.namelist():
                ss_root = ET.fromstring(zf.read("xl/sharedStrings.xml"))
                for si in ss_root.findall("main:si", _OPENXML_NS):
                    text_parts = [t.text or "" for t in si.findall(".//main:t", _OPENXML_NS)]
                    shared_strings.append("".join(text_parts))

            ws_root = ET.fromstring(zf.read(sheet_target))
            rows_out: list[list[Any]] = []
            for row_el in ws_root.findall(".//main:sheetData/main:row", _OPENXML_NS):
                row_cells: dict[int, Any] = {}
                max_col = -1
                for cell_el in row_el.findall("main:c", _OPENXML_NS):
                    cell_ref = cell_el.attrib.get("r", "")
                    match = re.match(r"^([A-Za-z]+)", cell_ref)
                    col_idx = _col_letters_to_index(match.group(1)) if match else max_col + 1
                    max_col = max(max_col, col_idx)
                    cell_type = cell_el.attrib.get("t")
                    v_el = cell_el.find("main:v", _OPENXML_NS)
                    is_el = cell_el.find("main:is", _OPENXML_NS)
                    val: Any = None
                    if cell_type == "s" and v_el is not None and v_el.text is not None:
                        val = shared_strings[int(v_el.text)]
                    elif cell_type == "inlineStr" and is_el is not None:
                        val = "".join(t.text or "" for t in is_el.findall(".//main:t", _OPENXML_NS))
                    elif v_el is not None and v_el.text is not None:
                        val = v_el.text
                    row_cells[col_idx] = val
                row_list = [row_cells.get(i) for i in range(max(max_col + 1, len(EXPECTED_HEADERS)))]
                rows_out.append(row_list)
            return rows_out


def parse_excel_workbook(workbook_path: Path) -> list[SourceProductRecord]:
    """Parse and validate the raw Excel workbook (`Sample Product Mapping`)."""
    if not workbook_path.exists():
        raise CatalogueDataValidationError(f"Workbook file not found: {workbook_path}")

    rows = _read_xlsx_sheet_rows(workbook_path, EXPECTED_SOURCE_SHEET)
    if not rows or list(rows[0][: len(EXPECTED_HEADERS)]) != EXPECTED_HEADERS:
        raise CatalogueDataValidationError(
            "Workbook headers do not match the expected 14-column schema"
        )

    errors: list[str] = []
    products: list[SourceProductRecord] = []
    seen_source_codes: set[str] = set()
    product_names_by_code: dict[str, str] = {}

    for excel_row, row in enumerate(rows[1:], start=2):
        if not any(cell is not None and str(cell).strip() for cell in row):
            continue
        code = str(row[0]).strip() if row[0] is not None else ""
        name = str(row[1]).strip() if row[1] is not None else ""

        if not code or not name:
            errors.append(f"Row {excel_row}: key product code or name is blank")
            continue

        if code in seen_source_codes:
            errors.append(f"Row {excel_row}: duplicate source product code '{code}'")
        seen_source_codes.add(code)

        if code in product_names_by_code and product_names_by_code[code] != name:
            errors.append(
                f"Row {excel_row}: conflicting product name for '{code}' "
                f"('{name}' vs '{product_names_by_code[code]}')"
            )
        product_names_by_code[code] = name

        mappings: list[SourceMappingEntry] = []
        for rank in EXPECTED_RANKS:
            name_idx = 2 + (rank - 1) * 2
            code_idx = name_idx + 1
            target_name = (
                str(row[name_idx]).strip()
                if name_idx < len(row) and row[name_idx] is not None
                else ""
            )
            target_code = (
                str(row[code_idx]).strip()
                if code_idx < len(row) and row[code_idx] is not None
                else ""
            )
            if not target_name or not target_code:
                errors.append(f"Row {excel_row}: missing target name/code at rank {rank}")
                continue

            if target_code in product_names_by_code and product_names_by_code[target_code] != target_name:
                errors.append(
                    f"Row {excel_row}: conflicting target name for '{target_code}' "
                    f"('{target_name}' vs '{product_names_by_code[target_code]}')"
                )
            product_names_by_code[target_code] = target_name

            mappings.append(
                SourceMappingEntry(
                    source_product_code=code,
                    recommended_product_code=target_code,
                    recommended_product_name=target_name,
                    recommendation_rank=rank,
                    source_excel_row=excel_row,
                )
            )

        products.append(
            SourceProductRecord(
                product_code=code,
                product_name=name,
                source_excel_row=excel_row,
                mappings=tuple(mappings),
            )
        )

    if errors:
        raise CatalogueDataValidationError("Workbook structural validation failed", errors)

    validate_dataset_records(products)
    return products


def parse_normalized_json(json_path: Path) -> list[SourceProductRecord]:
    """Parse normalized `product_mappings.json` into `SourceProductRecord` objects."""
    if not json_path.exists():
        raise CatalogueDataValidationError(f"Normalized JSON file not found: {json_path}")

    raw = json.loads(json_path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict) or "products" not in raw or not isinstance(raw["products"], list):
        raise CatalogueDataValidationError("Normalized JSON is missing 'products' list")

    records: list[SourceProductRecord] = []
    errors: list[str] = []

    for idx, item in enumerate(raw["products"]):
        code = str(item.get("product_code") or "").strip()
        name = str(item.get("product_name") or "").strip()
        excel_row = item.get("source_excel_row")
        raw_recs = item.get("recommendations")

        if not code or not name:
            errors.append(f"JSON product[{idx}]: blank product_code or product_name")
            continue
        if not isinstance(raw_recs, list):
            errors.append(f"JSON product '{code}': 'recommendations' must be a list")
            continue

        entries: list[SourceMappingEntry] = []
        for rec_idx, rec in enumerate(raw_recs):
            target_code = str(rec.get("product_code") or "").strip()
            target_name = str(rec.get("product_name") or "").strip()
            rank = rec.get("rank")
            if not isinstance(rank, int) or isinstance(rank, bool):
                errors.append(f"JSON product '{code}' rec[{rec_idx}]: invalid rank '{rank}'")
                continue
            entries.append(
                SourceMappingEntry(
                    source_product_code=code,
                    recommended_product_code=target_code,
                    recommended_product_name=target_name,
                    recommendation_rank=rank,
                    source_excel_row=excel_row,
                )
            )
        records.append(
            SourceProductRecord(
                product_code=code,
                product_name=name,
                source_excel_row=excel_row,
                mappings=tuple(entries),
            )
        )

    if errors:
        raise CatalogueDataValidationError("Normalized JSON parsing failed", errors)

    validate_dataset_records(records)
    return records


def validate_dataset_records(
    products: list[SourceProductRecord],
    *,
    catalogue_codes: set[str] | None = None,
) -> None:
    """Validate complete product and mapping integrity rules before any database write.

    Checks:
    - Non-empty product list.
    - No duplicate source product codes.
    - Consistent product names across source and target references.
    - Every source product has exactly 6 mappings with ranks 1..6.
    - Rank is strictly between 1 and 6.
    - Source and recommended product codes are not identical (no self-recommendations).
    - No duplicate (source_product_code, recommendation_rank) pairs.
    - No duplicate (source_product_code, recommended_product_code) pairs.
    - Every source and recommended product code resolves to the known product catalogue.
    """
    errors: list[str] = []
    if not products:
        raise CatalogueDataValidationError("Dataset contains 0 products")

    seen_sources: set[str] = set()
    name_registry: dict[str, str] = {}

    for product in products:
        code = product.product_code.strip()
        name = product.product_name.strip()
        if not code or not name:
            errors.append(f"Product has blank code or name: {product!r}")
            continue
        if code in seen_sources:
            errors.append(f"Duplicate source product code: '{code}'")
        seen_sources.add(code)

        if code in name_registry and name_registry[code] != name:
            errors.append(
                f"Conflicting product name for '{code}': '{name}' vs '{name_registry[code]}'"
            )
        name_registry[code] = name

    known_catalogue = catalogue_codes if catalogue_codes is not None else seen_sources

    seen_source_rank: set[tuple[str, int]] = set()
    seen_source_target: set[tuple[str, str]] = set()

    for product in products:
        source_code = product.product_code.strip()
        if source_code not in known_catalogue:
            errors.append(f"Missing source product in catalogue: '{source_code}'")

        if len(product.mappings) != 6:
            errors.append(
                f"Source product '{source_code}' must have exactly 6 mappings, found {len(product.mappings)}"
            )

        ranks_for_source: list[int] = []
        for entry in product.mappings:
            if entry.source_product_code != source_code:
                errors.append(
                    f"Mapping entry source '{entry.source_product_code}' does not match parent '{source_code}'"
                )
            target_code = entry.recommended_product_code.strip()
            target_name = entry.recommended_product_name.strip()
            rank = entry.recommendation_rank

            if not target_code or not target_name:
                errors.append(
                    f"Source '{source_code}' rank {rank}: blank recommended code or name"
                )

            if rank < 1 or rank > 6:
                errors.append(
                    f"Source '{source_code}' target '{target_code}': invalid rank {rank} (expected 1..6)"
                )
            ranks_for_source.append(rank)

            if source_code == target_code:
                errors.append(
                    f"Self-recommendation detected for '{source_code}' at rank {rank}"
                )

            rank_key = (source_code, rank)
            if rank_key in seen_source_rank:
                errors.append(
                    f"Duplicate (source_product_code, recommendation_rank) pair: ({source_code}, {rank})"
                )
            seen_source_rank.add(rank_key)

            target_key = (source_code, target_code)
            if target_key in seen_source_target:
                errors.append(
                    f"Duplicate (source_product_code, recommended_product_code) pair: ({source_code}, {target_code})"
                )
            seen_source_target.add(target_key)

            if target_code not in known_catalogue:
                errors.append(
                    f"Source '{source_code}' rank {rank} references missing recommended product '{target_code}'"
                )

            if target_code in name_registry and name_registry[target_code] != target_name:
                errors.append(
                    f"Conflicting product name for target '{target_code}': "
                    f"'{target_name}' vs '{name_registry[target_code]}'"
                )

        if sorted(ranks_for_source) != list(EXPECTED_RANKS):
            errors.append(
                f"Source product '{source_code}' does not contain complete ranks 1..6 (found {sorted(ranks_for_source)})"
            )

    if errors:
        raise CatalogueDataValidationError("Catalogue and mapping integrity validation failed", errors)


def verify_source_files_consistency(
    *,
    workbook_path: Path,
    normalized_json_path: Path,
    normalized_csv_path: Path,
    validation_report_path: Path | None = None,
) -> list[SourceProductRecord]:
    """Verify that the authoritative Excel workbook and Stage 1 normalized files agree 100%."""
    workbook_records = parse_excel_workbook(workbook_path)
    json_records = parse_normalized_json(normalized_json_path)

    discrepancies: list[str] = []

    if len(workbook_records) != 70:
        discrepancies.append(f"Expected 70 workbook products, found {len(workbook_records)}")

    total_workbook_mappings = sum(len(p.mappings) for p in workbook_records)
    if total_workbook_mappings != 420:
        discrepancies.append(f"Expected 420 workbook mappings, found {total_workbook_mappings}")

    if workbook_records != json_records:
        discrepancies.append(
            f"Discrepancy between workbook '{workbook_path.name}' and normalized JSON '{normalized_json_path.name}'"
        )

    if not normalized_csv_path.exists():
        discrepancies.append(f"Normalized CSV file not found: {normalized_csv_path}")
    else:
        with normalized_csv_path.open("r", newline="", encoding="utf-8") as csv_file:
            reader = list(csv.DictReader(csv_file))
        expected_csv_rows = [
            {
                "source_product_code": p.product_code,
                "source_product_name": p.product_name,
                "recommendation_rank": str(m.recommendation_rank),
                "recommended_product_code": m.recommended_product_code,
                "recommended_product_name": m.recommended_product_name,
            }
            for p in workbook_records
            for m in p.mappings
        ]
        if reader != expected_csv_rows:
            discrepancies.append(
                f"Discrepancy between workbook '{workbook_path.name}' and normalized CSV '{normalized_csv_path.name}'"
            )

    if validation_report_path is not None and validation_report_path.exists():
        report = json.loads(validation_report_path.read_text(encoding="utf-8"))
        expected_sha = report.get("source_sha256")
        actual_sha = hashlib.sha256(workbook_path.read_bytes()).hexdigest()
        if expected_sha and expected_sha != actual_sha:
            discrepancies.append(
                f"Workbook SHA-256 mismatch against validation_report.json: expected {expected_sha}, got {actual_sha}"
            )
        if report.get("unique_key_product_codes") != len(workbook_records):
            discrepancies.append("Workbook unique product count disagrees with validation_report.json")
        if report.get("recommendation_entries") != total_workbook_mappings:
            discrepancies.append("Workbook mapping entry count disagrees with validation_report.json")

    if discrepancies:
        raise CatalogueDataValidationError(
            "Source dataset cross-file verification failed",
            discrepancies,
        )

    return workbook_records


def load_synthetic_metadata_fixture(fixture_path: Path) -> dict[str, SyntheticMetadataRecord]:
    """Load explicitly labelled synthetic demonstration metadata fixture."""
    if not fixture_path.exists():
        raise CatalogueDataValidationError(f"Synthetic metadata fixture not found: {fixture_path}")

    raw = json.loads(fixture_path.read_text(encoding="utf-8"))
    if raw.get("fixture_type") != "SYNTHETIC_DEMONSTRATION_CATALOGUE_METADATA":
        raise CatalogueDataValidationError(
            "Synthetic fixture must declare fixture_type='SYNTHETIC_DEMONSTRATION_CATALOGUE_METADATA'"
        )

    records: dict[str, SyntheticMetadataRecord] = {}
    errors: list[str] = []
    for idx, item in enumerate(raw.get("products", [])):
        code = str(item.get("product_code") or "").strip()
        if not code:
            errors.append(f"Synthetic fixture item[{idx}] has blank product_code")
            continue
        if code in records:
            errors.append(f"Duplicate product_code '{code}' in synthetic metadata fixture")
            continue
        if item.get("is_synthetic_metadata") is not True or item.get("metadata_source") != "SYNTHETIC_FIXTURE":
            errors.append(
                f"Synthetic fixture item '{code}' must explicitly set "
                "is_synthetic_metadata=true and metadata_source='SYNTHETIC_FIXTURE'"
            )
            continue

        unit_price_raw = item.get("unit_price")
        unit_price = Decimal(str(unit_price_raw)) if unit_price_raw is not None else None
        records[code] = SyntheticMetadataRecord(
            product_code=code,
            is_rx=item.get("is_rx"),
            is_active=item.get("is_active"),
            available_quantity=item.get("available_quantity"),
            unit_price=unit_price,
            currency=str(item.get("currency") or "INR"),
            image_url=item.get("image_url"),
            product_url=item.get("product_url"),
        )

    if errors:
        raise CatalogueDataValidationError("Synthetic metadata fixture validation failed", errors)

    return records


class CatalogueLoaderService:
    """Transactional, idempotent loader for the 70 products and 420 ranked mappings."""

    def __init__(self, session: Session) -> None:
        self.session = session
        self.repository = ProductRepository(session)

    def load_records(
        self,
        records: list[SourceProductRecord],
        *,
        synthetic_metadata: dict[str, SyntheticMetadataRecord] | None = None,
        dry_run: bool = False,
        allow_name_updates: bool = False,
    ) -> CatalogueLoadSummary:
        """Validate and load product and mapping records transactionally.

        Data-integrity guarantees:
        - Pre-validates all products and mappings before database writes.
        - Without `synthetic_metadata`, products are seeded with `is_rx=None`,
          `is_active=None`, `available_quantity=None`, `unit_price=None`,
          `metadata_source=WORKBOOK_ONLY`, and `is_synthetic_metadata=False`.
        - Never overwrites an existing product whose `metadata_source == VERIFIED_CATALOGUE`
          with synthetic or workbook-only metadata.
        - Detects conflicting product names or conflicting existing mappings and rolls back
          the entire transaction rather than silently overwriting or skipping records.
        """
        summary = CatalogueLoadSummary(
            dry_run=dry_run,
            synthetic_fixture_applied=synthetic_metadata is not None,
            source_products_count=len(records),
            source_mappings_count=sum(len(r.mappings) for r in records),
        )

        # Include any pre-existing catalogue product codes in the DB so partial/custom loads
        # can also validate against existing products while ensuring self-consistency.
        existing_products = {p.product_code: p for p in self.repository.list_all(limit=10000)}
        known_codes = set(existing_products.keys()) | {r.product_code for r in records}
        validate_dataset_records(records, catalogue_codes=known_codes)

        try:
            # Step 1: Upsert products safely
            for source_product in records:
                code = source_product.product_code
                synth = synthetic_metadata.get(code) if synthetic_metadata else None
                existing = existing_products.get(code)

                if existing is None:
                    if synth is not None:
                        create_payload = ProductCreate(
                            product_code=code,
                            product_name=source_product.product_name,
                            is_rx=synth.is_rx,
                            is_active=synth.is_active,
                            available_quantity=synth.available_quantity,
                            unit_price=synth.unit_price,
                            currency=synth.currency,
                            image_url=synth.image_url,
                            product_url=synth.product_url,
                            metadata_source=CatalogueMetadataSource.SYNTHETIC_FIXTURE,
                            is_synthetic_metadata=True,
                        )
                    else:
                        create_payload = ProductCreate(
                            product_code=code,
                            product_name=source_product.product_name,
                            is_rx=None,
                            is_active=None,
                            available_quantity=None,
                            unit_price=None,
                            currency="INR",
                            image_url=None,
                            product_url=None,
                            metadata_source=CatalogueMetadataSource.WORKBOOK_ONLY,
                            is_synthetic_metadata=False,
                        )
                    created = self.repository.create(create_payload, commit=False)
                    existing_products[code] = created
                    summary.products_inserted += 1
                else:
                    # Check for conflicting product name
                    if existing.product_name != source_product.product_name and not allow_name_updates:
                        summary.products_rejected += 1
                        raise CatalogueDataValidationError(
                            f"Product name conflict for '{code}': database has "
                            f"'{existing.product_name}', source has '{source_product.product_name}'"
                        )

                    # Rule 7: Never overwrite existing VERIFIED_CATALOGUE metadata with synthetic or workbook values
                    if existing.metadata_source == CatalogueMetadataSource.VERIFIED_CATALOGUE:
                        summary.verified_metadata_preserved += 1
                        if existing.product_name != source_product.product_name and allow_name_updates:
                            self.repository.update(
                                existing,
                                ProductUpdate(product_name=source_product.product_name),
                                commit=False,
                            )
                            summary.products_updated += 1
                        else:
                            summary.products_unchanged += 1
                        continue

                    update_fields: dict[str, Any] = {}
                    if existing.product_name != source_product.product_name and allow_name_updates:
                        update_fields["product_name"] = source_product.product_name

                    if synth is not None:
                        desired_synth = {
                            "is_rx": synth.is_rx,
                            "is_active": synth.is_active,
                            "available_quantity": synth.available_quantity,
                            "unit_price": synth.unit_price,
                            "currency": synth.currency,
                            "image_url": synth.image_url,
                            "product_url": synth.product_url,
                            "metadata_source": CatalogueMetadataSource.SYNTHETIC_FIXTURE,
                            "is_synthetic_metadata": True,
                        }
                        for key, val in desired_synth.items():
                            if getattr(existing, key) != val:
                                update_fields[key] = val

                    if update_fields:
                        self.repository.update(
                            existing,
                            ProductUpdate(**update_fields),
                            commit=False,
                        )
                        summary.products_updated += 1
                    else:
                        summary.products_unchanged += 1

            # Step 2: Upsert 420 product mappings safely
            for source_product in records:
                existing_source_mappings = self.repository.list_mappings_for_source(
                    source_product.product_code
                )
                by_rank = {m.recommendation_rank: m for m in existing_source_mappings}
                by_target = {m.recommended_product_code: m for m in existing_source_mappings}

                for entry in source_product.mappings:
                    existing_rank_row = by_rank.get(entry.recommendation_rank)
                    existing_target_row = by_target.get(entry.recommended_product_code)

                    if existing_rank_row is None and existing_target_row is None:
                        self.repository.create_mapping(
                            ProductMappingCreate(
                                source_product_code=entry.source_product_code,
                                recommended_product_code=entry.recommended_product_code,
                                recommendation_rank=entry.recommendation_rank,
                                source_excel_row=entry.source_excel_row,
                            ),
                            commit=False,
                        )
                        summary.mappings_inserted += 1
                    elif (
                        existing_rank_row is not None
                        and existing_rank_row.recommended_product_code == entry.recommended_product_code
                    ):
                        if (
                            entry.source_excel_row is not None
                            and existing_rank_row.source_excel_row != entry.source_excel_row
                        ):
                            existing_rank_row.source_excel_row = entry.source_excel_row
                            self.session.add(existing_rank_row)
                            self.session.flush()
                            summary.mappings_updated += 1
                        else:
                            summary.mappings_unchanged += 1
                    else:
                        summary.mappings_rejected += 1
                        conflict_desc = (
                            f"Mapping conflict for source '{entry.source_product_code}' rank {entry.recommendation_rank}: "
                            f"source dataset maps to '{entry.recommended_product_code}', "
                            f"but database already contains conflicting mapping "
                            f"(rank_target={existing_rank_row.recommended_product_code if existing_rank_row else None}, "
                            f"target_rank={existing_target_row.recommendation_rank if existing_target_row else None})"
                        )
                        raise CatalogueDataValidationError(conflict_desc)

            if dry_run:
                self.session.rollback()
            else:
                self.session.commit()

            return summary
        except Exception:
            self.session.rollback()
            raise

    def load_from_files(
        self,
        *,
        workbook_path: Path,
        normalized_json_path: Path,
        normalized_csv_path: Path,
        validation_report_path: Path | None = None,
        synthetic_fixture_path: Path | None = None,
        apply_synthetic_metadata: bool = False,
        dry_run: bool = False,
    ) -> CatalogueLoadSummary:
        """Verify all source files and load into the database."""
        records = verify_source_files_consistency(
            workbook_path=workbook_path,
            normalized_json_path=normalized_json_path,
            normalized_csv_path=normalized_csv_path,
            validation_report_path=validation_report_path,
        )
        synthetic_map: dict[str, SyntheticMetadataRecord] | None = None
        if apply_synthetic_metadata:
            if synthetic_fixture_path is None:
                raise CatalogueDataValidationError(
                    "synthetic_fixture_path is required when apply_synthetic_metadata=True"
                )
            synthetic_map = load_synthetic_metadata_fixture(synthetic_fixture_path)

        return self.load_records(
            records,
            synthetic_metadata=synthetic_map,
            dry_run=dry_run,
        )


def verify_database_catalogue(
    session: Session,
    *,
    expected_workbook_codes: set[str] | None = None,
) -> DatabaseCatalogueVerificationReport:
    """Run SQL/ORM verification queries against the database to audit loaded catalogue and mappings."""
    all_products = list(session.scalars(select(Product).order_by(Product.product_code)).all())
    all_product_codes = {p.product_code for p in all_products}

    if expected_workbook_codes is not None:
        workbook_products_loaded = len(all_product_codes & expected_workbook_codes)
    else:
        workbook_products_loaded = sum(
            1 for p in all_products if p.product_code.startswith("SAMPLE-")
        )

    mapping_records_loaded = int(
        session.scalar(select(func.count(ProductMapping.id))) or 0
    )

    per_source_rows = session.execute(
        select(
            ProductMapping.source_product_code,
            func.count(ProductMapping.id),
        ).group_by(ProductMapping.source_product_code)
    ).all()

    distinct_sources = len(per_source_rows)
    counts = [int(row[1]) for row in per_source_rows]
    min_per_source = min(counts) if counts else 0
    max_per_source = max(counts) if counts else 0

    invalid_ranks = int(
        session.scalar(
            select(func.count(ProductMapping.id)).where(
                (ProductMapping.recommendation_rank < 1)
                | (ProductMapping.recommendation_rank > 6)
            )
        )
        or 0
    )

    self_recommendations = int(
        session.scalar(
            select(func.count(ProductMapping.id)).where(
                ProductMapping.source_product_code == ProductMapping.recommended_product_code
            )
        )
        or 0
    )

    dup_rank_groups = session.execute(
        select(
            ProductMapping.source_product_code,
            ProductMapping.recommendation_rank,
            func.count(ProductMapping.id),
        )
        .group_by(ProductMapping.source_product_code, ProductMapping.recommendation_rank)
        .having(func.count(ProductMapping.id) > 1)
    ).all()
    duplicate_source_rank_pairs = len(dup_rank_groups)

    dup_target_groups = session.execute(
        select(
            ProductMapping.source_product_code,
            ProductMapping.recommended_product_code,
            func.count(ProductMapping.id),
        )
        .group_by(ProductMapping.source_product_code, ProductMapping.recommended_product_code)
        .having(func.count(ProductMapping.id) > 1)
    ).all()
    duplicate_source_target_pairs = len(dup_target_groups)

    all_mappings = list(
        session.scalars(
            select(ProductMapping).order_by(
                ProductMapping.source_product_code.asc(),
                ProductMapping.recommendation_rank.asc(),
            )
        ).all()
    )

    missing_source_products = sum(
        1 for m in all_mappings if m.source_product_code not in all_product_codes
    )
    missing_recommended_products = sum(
        1 for m in all_mappings if m.recommended_product_code not in all_product_codes
    )

    ranks_by_source: dict[str, list[int]] = {}
    for m in all_mappings:
        ranks_by_source.setdefault(m.source_product_code, []).append(m.recommendation_rank)

    target_sources = expected_workbook_codes if expected_workbook_codes is not None else set(ranks_by_source.keys())
    sources_without_exact_6_ranks = sorted(
        code
        for code in target_sources
        if sorted(ranks_by_source.get(code, [])) != list(EXPECTED_RANKS)
    )

    workbook_only_products = sum(
        1 for p in all_products if p.metadata_source == CatalogueMetadataSource.WORKBOOK_ONLY
    )
    synthetic_metadata_products = sum(
        1
        for p in all_products
        if p.metadata_source == CatalogueMetadataSource.SYNTHETIC_FIXTURE or p.is_synthetic_metadata
    )
    verified_metadata_products = sum(
        1 for p in all_products if p.metadata_source == CatalogueMetadataSource.VERIFIED_CATALOGUE
    )
    unknown_eligibility_products = sum(
        1
        for p in all_products
        if p.is_rx is None or p.is_active is None or p.available_quantity is None
    )

    errors: list[str] = []
    if workbook_products_loaded != 70:
        errors.append(f"Expected 70 workbook products loaded, found {workbook_products_loaded}")
    if mapping_records_loaded != 420:
        errors.append(f"Expected 420 mapping records loaded, found {mapping_records_loaded}")
    if min_per_source != 6 or max_per_source != 6 or distinct_sources != 70:
        errors.append(
            f"Expected 70 sources with 6 mappings each, found {distinct_sources} sources (min={min_per_source}, max={max_per_source})"
        )
    if sources_without_exact_6_ranks:
        errors.append(
            f"Sources missing exact ranks 1..6: {', '.join(sources_without_exact_6_ranks)}"
        )
    if invalid_ranks != 0:
        errors.append(f"Found {invalid_ranks} mappings with rank outside 1..6")
    if self_recommendations != 0:
        errors.append(f"Found {self_recommendations} self-recommendations")
    if duplicate_source_rank_pairs != 0:
        errors.append(f"Found {duplicate_source_rank_pairs} duplicate (source, rank) pairs")
    if duplicate_source_target_pairs != 0:
        errors.append(f"Found {duplicate_source_target_pairs} duplicate (source, target) pairs")
    if missing_source_products != 0:
        errors.append(f"Found {missing_source_products} mappings with missing source product")
    if missing_recommended_products != 0:
        errors.append(f"Found {missing_recommended_products} mappings with missing recommended product")

    return DatabaseCatalogueVerificationReport(
        status="PASS" if not errors else "FAIL",
        workbook_products_loaded=workbook_products_loaded,
        total_catalogue_products=len(all_products),
        mapping_records_loaded=mapping_records_loaded,
        distinct_source_products_in_mappings=distinct_sources,
        mappings_per_source_min=min_per_source,
        mappings_per_source_max=max_per_source,
        sources_without_exact_6_ranks=sources_without_exact_6_ranks,
        invalid_ranks=invalid_ranks,
        self_recommendations=self_recommendations,
        duplicate_source_rank_pairs=duplicate_source_rank_pairs,
        duplicate_source_target_pairs=duplicate_source_target_pairs,
        missing_source_products=missing_source_products,
        missing_recommended_products=missing_recommended_products,
        workbook_only_products=workbook_only_products,
        synthetic_metadata_products=synthetic_metadata_products,
        verified_metadata_products=verified_metadata_products,
        unknown_eligibility_products=unknown_eligibility_products,
        errors=errors,
    )
