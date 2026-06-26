"""The eight tools. The model emits tool calls; this layer touches the sheet.

Two rules enforced here:
  1. Name-to-index resolution lives in this layer, never the model.
  2. Every tool reads the live header row, so it cannot drift after a
     structural change (the agent also re-reads structure between calls).
"""

from __future__ import annotations

from typing import Any

from . import config
from .sheet import SheetClient, col_letter


class SheetTools:
    def __init__(self, client: SheetClient | None = None) -> None:
        self.client = client or SheetClient()

    # ---- data tier ------------------------------------------------------

    def get_sheet_structure(self) -> dict:
        values = self.client.all_values()
        headers = values[0] if values else []
        data_rows = values[1:] if len(values) > 1 else []
        return {
            "headers": headers,
            "num_rows": len(data_rows),  # data rows, excluding the header
            "num_cols": len(headers),
            "sheet_id": self.client.sheet_id,  # numeric gid, not the tab name
            "sample_rows": data_rows[:5],
            "validations": self.client.read_validations(),
        }

    def read_range(self, a1: str) -> list[list[str]]:
        return self.client.read_range(a1)

    def update_cells(self, a1: str, values: list[list[Any]]) -> dict:
        updated = self.client.update_range(a1, values)
        return {"updated_cells": updated, "range": a1}

    def append_row(self, row: dict[str, Any]) -> dict:
        headers = self.client.headers()
        index = {h.strip().lower(): i for i, h in enumerate(headers)}
        ordered = [""] * len(headers)
        unknown = []
        for key, value in row.items():
            pos = index.get(str(key).strip().lower())
            if pos is None:
                unknown.append(key)
                continue
            ordered[pos] = value
        if unknown:
            raise ValueError(
                f"Unknown column(s) {unknown}. Current headers: {headers}"
            )
        target_row = len(self.client.all_values()) + 1
        end = col_letter(len(headers) - 1)
        a1 = f"A{target_row}:{end}{target_row}"
        self.client.update_range(a1, [ordered])
        return {"row_index": target_row, "values": ordered}

    # ---- schema tier ----------------------------------------------------

    def add_column(
        self,
        name: str,
        position: int,
        validation_options: list[str] | None = None,
    ) -> dict:
        """Insert a column at 1-based ``position`` (A=1) with header ``name``."""
        start = max(position - 1, 0)
        self.client.batch_update(
            [
                {
                    "insertDimension": {
                        "range": {
                            "sheetId": self.client.sheet_id,
                            "dimension": "COLUMNS",
                            "startIndex": start,
                            "endIndex": start + 1,
                        },
                        "inheritFromBefore": start > 0,
                    }
                }
            ]
        )
        header_cell = f"{col_letter(start)}1"
        self.client.update_range(header_cell, [[name]])
        result = {"added": name, "position": start + 1, "column": col_letter(start)}
        if validation_options:
            self._apply_validation(start, validation_options)
            result["validation_options"] = validation_options
        return result

    def remove_column(self, name: str, confirmed: bool = False) -> dict:
        """Delete a column by NAME. Refuses unless confirmed=True."""
        idx = self.client.column_index(name)
        values = self.client.all_values()
        value_count = sum(
            1 for r in values[1:] if idx < len(r) and str(r[idx]).strip()
        )
        if not confirmed:
            return {
                "needs_confirmation": True,
                "column": name,
                "letter": col_letter(idx),
                "value_count": value_count,
                "message": (
                    f"This will delete column {col_letter(idx)} '{name}' and its "
                    f"{value_count} values. Confirm to proceed."
                ),
            }
        self.client.batch_update(
            [
                {
                    "deleteDimension": {
                        "range": {
                            "sheetId": self.client.sheet_id,
                            "dimension": "COLUMNS",
                            "startIndex": idx,
                            "endIndex": idx + 1,
                        }
                    }
                }
            ]
        )
        return {"removed": name, "letter": col_letter(idx), "deleted_values": value_count}

    def rename_column(self, old: str, new: str) -> dict:
        idx = self.client.column_index(old)
        self.client.update_range(f"{col_letter(idx)}1", [[new]])
        return {"renamed_from": old, "renamed_to": new, "column": col_letter(idx)}

    def set_column_validation(
        self,
        column: str,
        options: list[str],
        reconcile: str = "remap",
    ) -> dict:
        """Apply a ONE_OF_LIST dropdown to a column and reconcile stale values.

        reconcile: "remap" stale values to a default, "clear" them, or
        "report" only counts them.
        """
        idx = self.client.column_index(column)
        self._apply_validation(idx, options)

        values = self.client.all_values()
        option_set = set(options)
        remap_to = "Not started" if "Not started" in option_set else options[0]
        invalid_rows = []
        for row_num, row in enumerate(values[1:], start=2):
            cell = row[idx] if idx < len(row) else ""
            if str(cell).strip() and cell not in option_set:
                invalid_rows.append(row_num)

        reconciled = 0
        if invalid_rows and reconcile in ("remap", "clear"):
            new_value = "" if reconcile == "clear" else remap_to
            updates = [
                {"range": f"{col_letter(idx)}{r}", "values": [[new_value]]}
                for r in invalid_rows
            ]
            self.client.ws.batch_update(updates, value_input_option="USER_ENTERED")
            reconciled = len(invalid_rows)

        return {
            "column": column,
            "letter": col_letter(idx),
            "options": options,
            "reconcile": reconcile,
            "invalid_values_found": len(invalid_rows),
            "reconciled": reconciled,
            "remap_target": remap_to if reconcile == "remap" else None,
        }

    def clean_placeholders(self, rules: list[dict] | None = None) -> dict:
        """Normalize placeholder cells and return an audit of what changed.

        Each rule: {"column", "match", "action" ("remap"|"clear"),
        "replacement"}. Defaults to config.DEFAULT_PLACEHOLDER_RULES, which
        remaps Application Status "N/A" -> "Not started" and clears Rejection
        Reason "N/A". Counts come from code, so the audit is trustworthy.
        """
        rules = rules if rules is not None else config.DEFAULT_PLACEHOLDER_RULES
        values = self.client.all_values()
        audit: dict[str, dict] = {}
        batch: list[dict] = []

        for rule in rules:
            column = rule["column"]
            match = str(rule.get("match", "N/A"))
            action = rule.get("action", "remap")
            replacement = rule.get("replacement", "Not started" if action == "remap" else "")
            try:
                idx = self.client.column_index(column)
            except ValueError as exc:
                audit[column] = {"changed": 0, "error": str(exc)}
                continue
            changed_rows = [
                row_num
                for row_num, row in enumerate(values[1:], start=2)
                if (row[idx] if idx < len(row) else "").strip() == match
            ]
            for r in changed_rows:
                batch.append(
                    {"range": f"{col_letter(idx)}{r}", "values": [[replacement]]}
                )
            audit[column] = {
                "changed": len(changed_rows),
                "action": action,
                "match": match,
                "replacement": replacement,
            }

        if batch:
            self.client.ws.batch_update(batch, value_input_option="USER_ENTERED")

        return {
            "audit": audit,
            "total_changed": sum(c.get("changed", 0) for c in audit.values()),
        }

    # ---- internal -------------------------------------------------------

    def _apply_validation(self, col_idx: int, options: list[str]) -> None:
        self.client.batch_update(
            [
                {
                    "setDataValidation": {
                        "range": {
                            "sheetId": self.client.sheet_id,
                            "startRowIndex": 1,
                            "startColumnIndex": col_idx,
                            "endColumnIndex": col_idx + 1,
                        },
                        "rule": {
                            "condition": {
                                "type": "ONE_OF_LIST",
                                "values": [
                                    {"userEnteredValue": o} for o in options
                                ],
                            },
                            "showCustomUi": True,
                            "strict": False,
                        },
                    }
                }
            ]
        )
