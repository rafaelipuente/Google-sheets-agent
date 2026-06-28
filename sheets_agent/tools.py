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
        """Write a new entry into the first row whose Company Name (anchor,
        column A) is blank; fall back to a true append if none is free.

        The template pre-fills "N/A" in the Status column for hundreds of empty
        rows, so emptiness is judged by Company Name only. Cells not supplied in
        ``row`` (notably the Status dropdown) keep whatever value is already
        there, rather than being blanked.
        """
        headers = self.client.headers()
        index = {h.strip().lower(): i for i, h in enumerate(headers)}
        unknown = [k for k in row if str(k).strip().lower() not in index]
        if unknown:
            raise ValueError(
                f"Unknown column(s) {unknown}. Current headers: {headers}"
            )
        provided = {index[str(k).strip().lower()]: v for k, v in row.items()}
        anchor_idx = index.get("company name", 0)

        values = self.client.all_values()
        target_row = None
        existing_row: list = []
        for row_num, data in enumerate(values[1:], start=2):
            anchor = data[anchor_idx] if anchor_idx < len(data) else ""
            if not str(anchor).strip():
                target_row, existing_row = row_num, data
                break
        if target_row is None:
            target_row = len(values) + 1

        ordered = [""] * len(headers)
        for i in range(min(len(existing_row), len(headers))):
            ordered[i] = existing_row[i]
        for pos, value in provided.items():
            ordered[pos] = value

        end = col_letter(len(headers) - 1)
        a1 = f"A{target_row}:{end}{target_row}"
        # Whole row written in one update call -> atomic, no half-written row.
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

        reconcile defaults to "remap": stale values (e.g. the template's "N/A")
        are rewritten to "Not started" in this same call -- never a separate
        step (see SPEC.md 4.1). "clear" blanks them; "report" only counts them.
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

    def update_entry(
        self,
        company: str,
        updates: dict[str, Any],
        row_index: int | None = None,
    ) -> dict:
        """Update an existing entry, located by Company Name (the anchor).

        Only the supplied fields change; every other cell (including the Status
        dropdown) is left as-is. If several rows share the company name, returns
        the matching row numbers and asks for a row_index instead of guessing.
        """
        headers = self.client.headers()
        index = {h.strip().lower(): i for i, h in enumerate(headers)}
        unknown = [k for k in updates if str(k).strip().lower() not in index]
        if unknown:
            raise ValueError(
                f"Unknown column(s) {unknown}. Current headers: {headers}"
            )

        values = self.client.all_values()
        target_row, existing = self._resolve_row(values, company, row_index)
        if target_row is None:
            return existing  # a needs_disambiguation / not-found payload

        ordered = [""] * len(headers)
        for i in range(min(len(existing), len(headers))):
            ordered[i] = existing[i]
        changed = {}
        for key, value in updates.items():
            pos = index[str(key).strip().lower()]
            changed[headers[pos]] = {"from": ordered[pos], "to": value}
            ordered[pos] = value

        end = col_letter(len(headers) - 1)
        # Whole row written in one update call -> atomic, no half-written row.
        self.client.update_range(f"A{target_row}:{end}{target_row}", [ordered])
        return {
            "row_index": target_row,
            "company": company,
            "changed": changed,
            "values": ordered,
        }

    def delete_row(
        self,
        company: str | None = None,
        row_index: int | None = None,
        confirmed: bool = False,
    ) -> dict:
        """Delete an entire row, located by Company Name or explicit row_index.

        Destructive: refuses unless confirmed=True, returning a preview first.
        """
        values = self.client.all_values()
        headers = self.client.headers()
        target_row, existing = self._resolve_row(values, company, row_index)
        if target_row is None:
            return existing  # needs_disambiguation / not-found payload

        preview = list(existing[: len(headers)])
        if not confirmed:
            return {
                "needs_confirmation": True,
                "row_index": target_row,
                "company": company,
                "values": preview,
                "message": (
                    f"This will delete row {target_row} ({preview}). "
                    "Confirm to proceed."
                ),
            }
        self.client.batch_update(
            [
                {
                    "deleteDimension": {
                        "range": {
                            "sheetId": self.client.sheet_id,
                            "dimension": "ROWS",
                            "startIndex": target_row - 1,
                            "endIndex": target_row,
                        }
                    }
                }
            ]
        )
        return {"deleted_row": target_row, "values": preview}

    # ---- internal -------------------------------------------------------

    def _resolve_row(
        self, values: list[list], company: str | None, row_index: int | None
    ):
        """Return (row_number, row_values). On ambiguity/not-found, returns
        (None, payload) describing the problem for the model to relay."""
        headers = self.client.headers()
        if row_index is not None:
            if 2 <= row_index <= len(values):
                return row_index, values[row_index - 1]
            return None, {
                "error": f"Row {row_index} is out of range (sheet has {len(values)} rows).",
            }
        if not company:
            return None, {"error": "Provide a company name or a row_index."}

        anchor_idx = {h.strip().lower(): i for i, h in enumerate(headers)}.get(
            "company name", 0
        )
        target = str(company).strip().lower()
        matches = [
            (row_num, row)
            for row_num, row in enumerate(values[1:], start=2)
            if (row[anchor_idx] if anchor_idx < len(row) else "").strip().lower() == target
        ]
        if not matches:
            return None, {"error": f"No entry with Company Name '{company}' found."}
        if len(matches) > 1:
            rows = [m[0] for m in matches]
            return None, {
                "needs_disambiguation": True,
                "company": company,
                "matching_rows": rows,
                "message": (
                    f"Multiple rows match '{company}': rows {rows}. "
                    "Specify which with row_index."
                ),
            }
        return matches[0]

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
