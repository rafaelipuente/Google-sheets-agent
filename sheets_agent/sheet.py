"""Low-level sheet client: bridges gspread (values) and the raw API (structure).

The numeric ``sheet_id`` (gid) is the bridge between the two APIs.
"""

from __future__ import annotations

from typing import Any

from . import auth, config


def col_letter(index_zero_based: int) -> str:
    """0 -> A, 25 -> Z, 26 -> AA."""
    n = index_zero_based + 1
    letters = ""
    while n > 0:
        n, rem = divmod(n - 1, 26)
        letters = chr(65 + rem) + letters
    return letters


class SheetClient:
    def __init__(self) -> None:
        self.gc, self.service = auth.build_clients()
        self.spreadsheet = self.gc.open_by_key(config.SPREADSHEET_ID)
        self.ws = self.spreadsheet.worksheet(config.WORKSHEET_NAME)
        self.sheet_id: int = self.ws.id  # numeric gid, not the tab name

    # ---- values ops (gspread) -------------------------------------------

    def headers(self) -> list[str]:
        row = self.ws.row_values(1)
        return [h for h in row]

    def all_values(self) -> list[list[str]]:
        return self.ws.get_all_values()

    def read_range(self, a1: str) -> list[list[str]]:
        return self.ws.get(a1)

    def update_range(self, a1: str, values: list[list[Any]]) -> int:
        result = self.ws.update(a1, values, value_input_option="USER_ENTERED")
        return int(result.get("updatedCells", 0))

    # ---- structure (raw API) --------------------------------------------

    def batch_update(self, requests: list[dict]) -> dict:
        body = {"requests": requests}
        return (
            self.service.spreadsheets()
            .batchUpdate(spreadsheetId=config.SPREADSHEET_ID, body=body)
            .execute()
        )

    def column_index(self, name: str) -> int:
        """Resolve a header NAME to its current 0-based column index.

        Name-to-index resolution lives here, never in the model.
        """
        headers = self.headers()
        lowered = [h.strip().lower() for h in headers]
        try:
            return lowered.index(name.strip().lower())
        except ValueError as exc:
            raise ValueError(
                f"Column '{name}' not found. Current headers: {headers}"
            ) from exc

    def read_validations(self) -> dict[str, list[str]]:
        """Return {header_name: [ONE_OF_LIST options]} by inspecting row 2 cells."""
        headers = self.headers()
        if not headers:
            return {}
        resp = (
            self.service.spreadsheets()
            .get(
                spreadsheetId=config.SPREADSHEET_ID,
                ranges=[f"{config.WORKSHEET_NAME}!A2:{col_letter(len(headers) - 1)}2"],
                includeGridData=True,
                fields="sheets(data(rowData(values(dataValidation))))",
            )
            .execute()
        )
        validations: dict[str, list[str]] = {}
        try:
            row_data = resp["sheets"][0]["data"][0]["rowData"][0]["values"]
        except (KeyError, IndexError):
            return validations
        for idx, cell in enumerate(row_data):
            rule = cell.get("dataValidation")
            if not rule:
                continue
            cond = rule.get("condition", {})
            if cond.get("type") == "ONE_OF_LIST":
                opts = [v.get("userEnteredValue", "") for v in cond.get("values", [])]
                if idx < len(headers):
                    validations[headers[idx]] = opts
        return validations
