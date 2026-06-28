"""Low-level sheet client: bridges gspread (values) and the raw API (structure).

The numeric ``sheet_id`` (gid) is the bridge between the two APIs.
"""

from __future__ import annotations

from typing import Any

from . import auth, config
from .retry import with_retry


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
        self.spreadsheet = with_retry(
            lambda: self.gc.open_by_key(config.SPREADSHEET_ID), label="open spreadsheet"
        )
        self.ws = with_retry(
            lambda: self.spreadsheet.worksheet(config.WORKSHEET_NAME),
            label="open worksheet",
        )
        self.sheet_id: int = self.ws.id  # numeric gid, not the tab name

    # ---- values ops (gspread) -------------------------------------------

    def headers(self) -> list[str]:
        row = with_retry(lambda: self.ws.row_values(1), label="read headers")
        return [h for h in row]

    def all_values(self) -> list[list[str]]:
        return with_retry(self.ws.get_all_values, label="read all values")

    def read_range(self, a1: str) -> list[list[str]]:
        return with_retry(lambda: self.ws.get(a1), label="read range")

    def update_range(self, a1: str, values: list[list[Any]]) -> int:
        # One values.update call writes the entire range in a single request,
        # so a row write is atomic -- it cannot leave a half-written row.
        result = with_retry(
            lambda: self.ws.update(a1, values, value_input_option="USER_ENTERED"),
            label="write range",
        )
        return int(result.get("updatedCells", 0))

    # ---- structure (raw API) --------------------------------------------

    def batch_update(self, requests: list[dict]) -> dict:
        body = {"requests": requests}
        return with_retry(
            lambda: self.service.spreadsheets()
            .batchUpdate(spreadsheetId=config.SPREADSHEET_ID, body=body)
            .execute(),
            label="batch update",
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
        resp = with_retry(
            lambda: self.service.spreadsheets()
            .get(
                spreadsheetId=config.SPREADSHEET_ID,
                ranges=[f"{config.WORKSHEET_NAME}!A2:{col_letter(len(headers) - 1)}2"],
                includeGridData=True,
                fields="sheets(data(rowData(values(dataValidation))))",
            )
            .execute(),
            label="read validations",
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
