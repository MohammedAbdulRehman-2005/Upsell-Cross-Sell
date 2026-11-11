# Dataset validation report

**Status: PASS**

- Source: `Business_A_Synthetic_Product_Mapping_No_Quantity.xlsx`
- Sheet: `Sample Product Mapping`
- Data rows: 70
- Unique key product codes: 70
- Recommendation entries: 420
- Unique target product codes: 70
- Validation errors: 0

## Checks performed

- Required headers and 14-column layout
- Non-empty key product codes and names
- Unique key product codes
- Six complete recommendation pairs per key product
- No self-recommendations
- No duplicate recommendation code within a source row
- No conflicting product names for a product code
- Every recommendation target has a key-product record in this workbook

## Limitations

- The workbook contains no order quantities.
- The workbook contains no Rx/category attribute.
- The workbook contains no inventory/availability or active/inactive attribute.
- Positions 1–6 are ordinal ranking positions, not co-purchase counts, confidence scores, or probabilities.
