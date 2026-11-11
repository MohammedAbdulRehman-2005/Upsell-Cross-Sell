# Stage 3 — Product Catalogue Seeding & Mapping Data Integrity

## Overview

Stage 3 implements the auditable, repeatable, transactional data-loading pipeline that populates PostgreSQL with the **70 authoritative workbook products** and **420 ranked co-purchase mappings** from `Business_A_Synthetic_Product_Mapping_No_Quantity.xlsx` (`data/raw/product_mapping_source.xlsx`).

## Authoritative Source Files & Pre-Load Cross-Verification

Before any database write occurs, [`verify_source_files_consistency`](file:///d:/plural_upsell_assignment/backend/app/services/catalogue_loader.py) cross-verifies all four Stage 1 artifacts:

1. `data/raw/product_mapping_source.xlsx` (sheet `Sample Product Mapping`, SHA-256 `9660c7f20b55aba8196eedf2eb40b732b1814eb2b56bc5ec7417fcfec96f0d83`)
2. `data/normalized/product_mappings.json` (`SHA-256: 6caf6ee750b3afb87f06368792948ea6545c624f04a65aab1224a120094b96c6`)
3. `data/normalized/product_mappings.csv` (`SHA-256: af7376ecf13dcdca7477552500499269da2420eed30ce273b45b4ad82553130e`)
4. `data/normalized/validation_report.json`

If any discrepancy exists between the workbook, JSON, CSV, or SHA-256 checksum, the loader aborts immediately with `CatalogueDataValidationError` and performs zero database modifications.

### Verified Dataset Counts

| Metric | Value |
|---|---:|
| Unique key products (`SAMPLE-001` .. `SAMPLE-070`) | 70 |
| Recommendation ranks per product (`1..6`) | 6 |
| Total ranked mapping rows | 420 |
| Isolated mapping clusters (`7` SKUs per cluster) | 10 |
| Duplicate key product codes | 0 |
| Self-recommendations | 0 |
| Duplicate recommendations within a source row | 0 |
| Unresolved target product codes | 0 |

## Separation of Workbook Data, Unknown Metadata, Synthetic Fixtures, and Verified Catalogue Data

> [!IMPORTANT]
> The supplied workbook `Business_A_Synthetic_Product_Mapping_No_Quantity.xlsx` is a product-mapping sheet only. It does **not** contain authoritative inventory/stock, unit pricing, active/inactive status, or Rx/prescription classification.

To enforce strict data integrity and support the Stage 1 fail-closed rule (`CATALOGUE_METADATA_MISSING`):

1. **Workbook-Only Seeding (Default)**:
   - Running `python -m app.scripts.seed_catalogue` loads `product_code` and `product_name` from the workbook and leaves `is_rx = NULL`, `is_active = NULL`, `available_quantity = NULL`, and `unit_price = NULL`.
   - Sets `metadata_source = "WORKBOOK_ONLY"` and `is_synthetic_metadata = FALSE`.
   - Unknown eligibility metadata remains explicitly `NULL` (unknown) and is **never** silently defaulted to eligible (`is_rx=false`, `available_quantity > 0`) or inferred from product-name text.
2. **Explicit Synthetic Demonstration Fixture (`--apply-synthetic-fixture`)**:
   - Stored separately in `data/fixtures/synthetic_catalogue_metadata.json` with `fixture_type = "SYNTHETIC_DEMONSTRATION_CATALOGUE_METADATA"`.
   - When explicitly requested via `--apply-synthetic-fixture`, sets `metadata_source = "SYNTHETIC_FIXTURE"` and `is_synthetic_metadata = TRUE` so every synthetic record is unambiguously identifiable in SQL queries and audit traces.
3. **Protection of Verified Catalogue Metadata (`VERIFIED_CATALOGUE`)**:
   - If a product record in PostgreSQL has `metadata_source = "VERIFIED_CATALOGUE"`, subsequent executions of the seed script (even with `--apply-synthetic-fixture`) **never overwrite** its verified `is_rx`, `is_active`, `available_quantity`, or `unit_price` values.

## Idempotency & Transaction Safety

- **Single Transaction Boundary**: Both the 70 products and 420 mappings are processed inside a single database transaction. Any validation error, product-name conflict, or mapping rank/target conflict triggers `session.rollback()` and exits with status `1`.
- **Safe Upsert Semantics**:
  - Uses `product_code` as the natural identifier for products and `(source_product_code, recommendation_rank)` / `(source_product_code, recommended_product_code)` for mappings.
  - Re-running the seed command reports `products_inserted = 0`, `mappings_inserted = 0`, `products_unchanged = 70`, `mappings_unchanged = 420`.
  - Never deletes unrelated broader-catalogue products (e.g., SKUs outside the 70 mapped products).
- **Dry-Run Mode (`--dry-run`)**:
  - Executes full cross-file verification, integrity checks, and ORM flushes, then rolls back the transaction without modifying the database.

## CLI Commands

```powershell
# 1. Dry-run validation without modifying the database
docker compose exec backend python -m app.scripts.seed_catalogue --dry-run

# 2. Seed authoritative workbook products (70) and mappings (420) with unknown metadata preserved
docker compose exec backend python -m app.scripts.seed_catalogue

# 3. Optional: Seed with explicitly labelled synthetic demonstration metadata fixture
docker compose exec backend python -m app.scripts.seed_catalogue --apply-synthetic-fixture

# 4. Run standalone database catalogue & mapping integrity verification
docker compose exec backend python -m app.scripts.validate_database_catalogue
```
