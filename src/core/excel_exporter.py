"""
Excel export utility using pandas and openpyxl.
"""

from typing import Dict, List
import pandas as pd


def export_records_to_excel(
    file_path: str,
    columns: List[str],
    records: List[Dict[str, str]],
) -> int:
    """
    Export verified table records into an Excel (.xlsx) workbook using pandas and openpyxl.
    Auto-adjusts column widths and freezes the header row for readability.
    Returns the number of exported rows.
    """
    ordered_rows: List[Dict[str, str]] = [
        {col: row.get(col, "") for col in columns} for row in records
    ]

    df = pd.DataFrame(ordered_rows, columns=columns)
    with pd.ExcelWriter(file_path, engine="openpyxl") as writer:
        sheet_name = "Verified_FAAS_Data"
        df.to_excel(writer, index=False, sheet_name=sheet_name)
        worksheet = writer.sheets[sheet_name]
        worksheet.freeze_panes = "A2"

        for col_cells in worksheet.columns:
            max_len = max([len(str(cell.value or "")) for cell in col_cells] + [12])
            col_letter = col_cells[0].column_letter
            worksheet.column_dimensions[col_letter].width = min(max_len + 4, 55)

    return len(ordered_rows)

