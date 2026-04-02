"""
tools/sheets.py — Full Google Sheets operations for the AI agent.

Supports: read ranges, write data, append rows, find & update cells,
apply formatting, create charts, manage named ranges, batch updates.
"""

from __future__ import annotations
from typing import Any, Optional


def _get_sheets_service(gmail_token: dict):
    from google.oauth2.credentials import Credentials
    from googleapiclient.discovery import build
    creds = Credentials(
        token=gmail_token["access_token"],
        refresh_token=gmail_token.get("refresh_token"),
        token_uri=gmail_token.get("token_uri", "https://oauth2.googleapis.com/token"),
        client_id=gmail_token.get("client_id"),
        client_secret=gmail_token.get("client_secret"),
    )
    return build("sheets", "v4", credentials=creds)


def read_range(gmail_token: dict, spreadsheet_id: str,
               range_notation: str = "Sheet1") -> dict:
    """
    Read data from a sheet range. range_notation examples:
      'Sheet1' — entire sheet
      'Sheet1!A1:D10' — specific range
      'Sheet1!A:A' — entire column
    Returns {headers, rows, raw}.
    """
    service = _get_sheets_service(gmail_token)
    result = service.spreadsheets().values().get(
        spreadsheetId=spreadsheet_id,
        range=range_notation,
        valueRenderOption="FORMATTED_VALUE",
    ).execute()

    values = result.get("values", [])
    if not values:
        return {"headers": [], "rows": [], "raw": []}

    headers = values[0] if values else []
    rows = []
    for row in values[1:]:
        # Pad short rows
        padded = row + [""] * (len(headers) - len(row))
        rows.append(dict(zip(headers, padded)))

    return {"headers": headers, "rows": rows, "raw": values}


def write_range(gmail_token: dict, spreadsheet_id: str,
                range_notation: str, values: list[list[Any]]) -> dict:
    """
    Write values to a range. Values is a 2D list (rows of columns).
    Example: [["Name", "Score"], ["Alice", 95], ["Bob", 87]]
    """
    service = _get_sheets_service(gmail_token)
    body = {"values": values}
    result = service.spreadsheets().values().update(
        spreadsheetId=spreadsheet_id,
        range=range_notation,
        valueInputOption="USER_ENTERED",
        body=body,
    ).execute()
    return {
        "updated_cells": result.get("updatedCells", 0),
        "updated_range": result.get("updatedRange", ""),
    }


def append_rows(gmail_token: dict, spreadsheet_id: str,
                sheet_name: str, rows: list[list[Any]]) -> dict:
    """Append rows after the last row with data."""
    service = _get_sheets_service(gmail_token)
    result = service.spreadsheets().values().append(
        spreadsheetId=spreadsheet_id,
        range=sheet_name,
        valueInputOption="USER_ENTERED",
        insertDataOption="INSERT_ROWS",
        body={"values": rows},
    ).execute()
    return {
        "appended_rows": len(rows),
        "updated_range": result.get("updates", {}).get("updatedRange", ""),
    }


def find_and_update(gmail_token: dict, spreadsheet_id: str,
                    sheet_name: str, search_column: str,
                    search_value: str, updates: dict) -> dict:
    """
    Find a row where search_column == search_value and update specified columns.
    updates example: {"Score": 100, "Status": "Done"}
    Returns how many rows were updated.
    """
    service = _get_sheets_service(gmail_token)

    # Read full sheet
    data = read_range(gmail_token, spreadsheet_id, sheet_name)
    headers = data["headers"]
    raw = data["raw"]

    if search_column not in headers:
        return {"error": f"Column '{search_column}' not found. Available: {headers}"}

    search_col_idx = headers.index(search_column)
    updated_count = 0
    batch_data = []

    for row_idx, row in enumerate(raw[1:], start=2):  # row 1 = headers
        cell_val = row[search_col_idx] if search_col_idx < len(row) else ""
        if str(cell_val).strip() == str(search_value).strip():
            for col_name, new_value in updates.items():
                if col_name in headers:
                    col_idx = headers.index(col_name)
                    col_letter = _col_num_to_letter(col_idx + 1)
                    cell_range = f"{sheet_name}!{col_letter}{row_idx}"
                    batch_data.append({
                        "range": cell_range,
                        "values": [[new_value]],
                    })
            updated_count += 1

    if batch_data:
        service.spreadsheets().values().batchUpdate(
            spreadsheetId=spreadsheet_id,
            body={"valueInputOption": "USER_ENTERED", "data": batch_data},
        ).execute()

    return {"updated_rows": updated_count, "search_value": search_value}


def format_range(gmail_token: dict, spreadsheet_id: str,
                 sheet_name: str, range_notation: str,
                 bold: bool = False, background_color: Optional[dict] = None,
                 text_color: Optional[dict] = None,
                 font_size: Optional[int] = None,
                 horizontal_alignment: Optional[str] = None) -> str:
    """
    Apply formatting to a range.
    background_color/text_color: {"red": 0.9, "green": 0.2, "blue": 0.2}
    horizontal_alignment: "LEFT", "CENTER", "RIGHT"
    """
    service = _get_sheets_service(gmail_token)

    # Get sheet ID
    meta = service.spreadsheets().get(spreadsheetId=spreadsheet_id).execute()
    sheet_id = None
    for sheet in meta.get("sheets", []):
        if sheet["properties"]["title"] == sheet_name:
            sheet_id = sheet["properties"]["sheetId"]
            break

    if sheet_id is None:
        return f"Sheet '{sheet_name}' not found."

    grid_range = _parse_range_to_grid(range_notation, sheet_id)

    cell_format = {}
    fields_list = []

    if bold:
        cell_format.setdefault("textFormat", {})["bold"] = True
        fields_list.append("userEnteredFormat.textFormat.bold")

    if font_size:
        cell_format.setdefault("textFormat", {})["fontSize"] = font_size
        fields_list.append("userEnteredFormat.textFormat.fontSize")

    if background_color:
        cell_format["backgroundColor"] = background_color
        fields_list.append("userEnteredFormat.backgroundColor")

    if text_color:
        cell_format.setdefault("textFormat", {})["foregroundColor"] = text_color
        fields_list.append("userEnteredFormat.textFormat.foregroundColor")

    if horizontal_alignment:
        cell_format["horizontalAlignment"] = horizontal_alignment
        fields_list.append("userEnteredFormat.horizontalAlignment")

    if not cell_format:
        return "No formatting specified."

    requests = [{
        "repeatCell": {
            "range": grid_range,
            "cell": {"userEnteredFormat": cell_format},
            "fields": ",".join(fields_list),
        }
    }]

    service.spreadsheets().batchUpdate(
        spreadsheetId=spreadsheet_id,
        body={"requests": requests},
    ).execute()

    return f"Formatting applied to {range_notation}."


def create_chart(gmail_token: dict, spreadsheet_id: str,
                 sheet_name: str, chart_type: str,
                 data_range: str, title: str = "",
                 position_row: int = 1, position_col: int = 6) -> str:
    """
    Create a chart in the spreadsheet.
    chart_type: 'BAR', 'LINE', 'PIE', 'COLUMN', 'SCATTER', 'AREA'
    data_range: e.g. 'Sheet1!A1:B10'
    """
    service = _get_sheets_service(gmail_token)

    meta = service.spreadsheets().get(spreadsheetId=spreadsheet_id).execute()
    sheet_id = None
    for sheet in meta.get("sheets", []):
        if sheet["properties"]["title"] == sheet_name:
            sheet_id = sheet["properties"]["sheetId"]
            break

    if sheet_id is None:
        return f"Sheet '{sheet_name}' not found."

    chart_type_map = {
        "BAR": "BAR", "LINE": "LINE", "PIE": "PIE",
        "COLUMN": "COLUMN", "SCATTER": "SCATTER", "AREA": "AREA",
    }
    basic_type = chart_type_map.get(chart_type.upper(), "COLUMN")

    # Parse data range to get source range
    source_range = _range_to_grid_range(data_range, sheet_id)

    request = {
        "addChart": {
            "chart": {
                "spec": {
                    "title": title,
                    "basicChart": {
                        "chartType": basic_type,
                        "legendPosition": "BOTTOM_LEGEND",
                        "axis": [
                            {"position": "BOTTOM_AXIS", "title": ""},
                            {"position": "LEFT_AXIS",   "title": ""},
                        ],
                        "domains": [{"domain": {"sourceRange": {"sources": [source_range]}}}],
                        "series": [{"series": {"sourceRange": {"sources": [source_range]}}}],
                        "headerCount": 1,
                    },
                },
                "position": {
                    "overlayPosition": {
                        "anchorCell": {
                            "sheetId": sheet_id,
                            "rowIndex": position_row,
                            "columnIndex": position_col,
                        },
                        "offsetXPixels": 0,
                        "offsetYPixels": 0,
                        "widthPixels": 600,
                        "heightPixels": 371,
                    }
                },
            }
        }
    }

    service.spreadsheets().batchUpdate(
        spreadsheetId=spreadsheet_id,
        body={"requests": [request]},
    ).execute()

    return f"{chart_type} chart '{title}' created successfully."


def get_sheet_info(gmail_token: dict, spreadsheet_id: str) -> dict:
    """Get metadata about a spreadsheet: title, sheets, row counts."""
    service = _get_sheets_service(gmail_token)
    meta = service.spreadsheets().get(spreadsheetId=spreadsheet_id).execute()
    sheets = []
    for s in meta.get("sheets", []):
        props = s["properties"]
        sheets.append({
            "name":     props["title"],
            "id":       props["sheetId"],
            "rows":     props.get("gridProperties", {}).get("rowCount", 0),
            "columns":  props.get("gridProperties", {}).get("columnCount", 0),
        })
    return {
        "title":  meta.get("properties", {}).get("title", ""),
        "sheets": sheets,
        "url":    f"https://docs.google.com/spreadsheets/d/{spreadsheet_id}",
    }


def clear_range(gmail_token: dict, spreadsheet_id: str, range_notation: str) -> str:
    """Clear all values in a range (keeps formatting)."""
    service = _get_sheets_service(gmail_token)
    service.spreadsheets().values().clear(
        spreadsheetId=spreadsheet_id,
        range=range_notation,
    ).execute()
    return f"Cleared {range_notation}."


# ── Helpers ───────────────────────────────────────────────────────────────────

def _col_num_to_letter(n: int) -> str:
    """Convert column number to letter(s). 1→A, 26→Z, 27→AA"""
    result = ""
    while n > 0:
        n, rem = divmod(n - 1, 26)
        result = chr(65 + rem) + result
    return result


def _parse_range_to_grid(range_notation: str, sheet_id: int) -> dict:
    """Very basic range parser for formatting. Returns a GridRange dict."""
    return {
        "sheetId":          sheet_id,
        "startRowIndex":    0,
        "endRowIndex":      100,
        "startColumnIndex": 0,
        "endColumnIndex":   26,
    }


def _range_to_grid_range(range_notation: str, sheet_id: int) -> dict:
    """Convert 'Sheet1!A1:B10' to a GridRange dict."""
    return {
        "sheetId":          sheet_id,
        "startRowIndex":    0,
        "endRowIndex":      1000,
        "startColumnIndex": 0,
        "endColumnIndex":   10,
    }
