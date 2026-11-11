"""Validate and normalize the supplied synthetic product-mapping workbook."""
from __future__ import annotations

import argparse
import csv
import json
from collections import defaultdict
from pathlib import Path
from openpyxl import load_workbook

EXPECTED_HEADERS = [
    "FG Code", "Product Name",
    "Together Sold Product 1", "FG Code Product 1",
    "Together Sold Product 2", "FG Code Product 2",
    "Together Sold Product 3", "FG Code Product 3",
    "Together Sold Product 4", "FG Code Product 4",
    "Together Sold Product 5", "FG Code Product 5",
    "Together Sold Product 6", "FG Code Product 6",
]


def normalize(source: Path, output_dir: Path) -> dict:
    workbook = load_workbook(source, data_only=True)
    if "Sample Product Mapping" not in workbook.sheetnames:
        raise ValueError("Required sheet 'Sample Product Mapping' is missing")
    sheet = workbook["Sample Product Mapping"]
    values = list(sheet.iter_rows(values_only=True))
    if not values or list(values[0]) != EXPECTED_HEADERS:
        raise ValueError("Workbook headers do not match the expected 14-column schema")

    errors: list[str] = []
    products: list[dict] = []
    source_codes: set[str] = set()
    target_names: dict[str, str] = {}

    for excel_row, row in enumerate(values[1:], start=2):
        if not any(value is not None and str(value).strip() for value in row):
            continue
        code = str(row[0]).strip() if row[0] is not None else ""
        name = str(row[1]).strip() if row[1] is not None else ""
        if not code or not name:
            errors.append(f"Row {excel_row}: key product code/name is blank")
        if code in source_codes:
            errors.append(f"Row {excel_row}: duplicate source code {code}")
        source_codes.add(code)

        recommendations = []
        local_codes: set[str] = set()
        for rank in range(1, 7):
            name_index = 2 + (rank - 1) * 2
            code_index = name_index + 1
            target_name = str(row[name_index]).strip() if row[name_index] is not None else ""
            target_code = str(row[code_index]).strip() if row[code_index] is not None else ""
            if not target_name or not target_code:
                errors.append(f"Row {excel_row}: missing target name/code at rank {rank}")
                continue
            if target_code == code:
                errors.append(f"Row {excel_row}: self-recommendation {code}")
            if target_code in local_codes:
                errors.append(f"Row {excel_row}: duplicate target {target_code}")
            local_codes.add(target_code)
            if target_code in target_names and target_names[target_code] != target_name:
                errors.append(f"Conflicting names for target code {target_code}")
            target_names[target_code] = target_name
            recommendations.append({
                "rank": rank,
                "product_code": target_code,
                "product_name": target_name,
            })
        if len(recommendations) != 6:
            errors.append(f"Row {excel_row}: expected 6 recommendations, found {len(recommendations)}")
        products.append({
            "product_code": code,
            "product_name": name,
            "recommendations": recommendations,
            "source_excel_row": excel_row,
        })

    missing_targets = sorted(set(target_names) - source_codes)
    if missing_targets:
        errors.append("Targets without source-product records: " + ", ".join(missing_targets))
    if errors:
        raise ValueError("Dataset validation failed:\n- " + "\n- ".join(errors))

    output_dir.mkdir(parents=True, exist_ok=True)
    normalized = {
        "schema_version": "1.0",
        "source_file": source.name,
        "source_sheet": sheet.title,
        "mapping_semantics": "Positions are ordinal ranks, not counts or probabilities.",
        "products": products,
    }
    (output_dir / "product_mappings.json").write_text(
        json.dumps(normalized, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    with (output_dir / "product_mappings.csv").open("w", newline="", encoding="utf-8") as file:
        writer = csv.writer(file)
        writer.writerow([
            "source_product_code", "source_product_name", "recommendation_rank",
            "recommended_product_code", "recommended_product_name",
        ])
        for product in products:
            for recommendation in product["recommendations"]:
                writer.writerow([
                    product["product_code"], product["product_name"],
                    recommendation["rank"], recommendation["product_code"],
                    recommendation["product_name"],
                ])

    return {
        "status": "PASS",
        "source_products": len(products),
        "recommendation_entries": sum(len(p["recommendations"]) for p in products),
        "unique_target_codes": len(target_names),
        "validation_errors": 0,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, default=Path("data/raw/product_mapping_source.xlsx"))
    parser.add_argument("--output", type=Path, default=Path("data/normalized"))
    args = parser.parse_args()
    print(json.dumps(normalize(args.source, args.output), indent=2))
