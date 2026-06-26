"""Tool-layer tests with a fake sheet client (no Google connection).

Focus on the spec's crux: name-to-index resolution, zero-based structural
indices, validation re-apply, and audited cleanup counts.
"""

import unittest

from sheets_agent.tools import SheetTools
from sheets_agent.sheet import col_letter


HEADERS = ["Company Name", "Application Status", "Role", "Salary", "Rejection Reason"]
DATA = [
    ["Acme", "N/A", "SWE", "100k", "N/A"],
    ["Beta", "Applied", "PM", "", ""],
    ["Gamma", "N/A", "QA", "90k", "ghosted"],
]
CANONICAL = [
    "Not started", "To apply", "Applied", "OA received",
    "Interviewing", "Offer", "Rejected", "Ghosted (30+ days, no response)",
]


class FakeWorksheet:
    def __init__(self):
        self.batch_updates = []

    def batch_update(self, updates, value_input_option=None):
        self.batch_updates.append(updates)


class FakeSheetClient:
    """Records structural requests and value writes; serves canned grid."""

    sheet_id = 999

    def __init__(self):
        self.grid = [list(HEADERS)] + [list(r) for r in DATA]
        self.batch_requests = []
        self.updates = []
        self.ws = FakeWorksheet()

    def headers(self):
        return self.grid[0]

    def all_values(self):
        return self.grid

    def read_range(self, a1):
        return [["x"]]

    def read_validations(self):
        return {"Application Status": ["Not started", "Applied"]}

    def update_range(self, a1, values):
        self.updates.append((a1, values))
        return sum(len(r) for r in values)

    def batch_update(self, requests):
        self.batch_requests.append(requests)
        return {"ok": True}

    def column_index(self, name):
        lowered = [h.strip().lower() for h in self.headers()]
        try:
            return lowered.index(name.strip().lower())
        except ValueError as exc:
            raise ValueError(f"Column '{name}' not found.") from exc


class ToolTests(unittest.TestCase):
    def setUp(self):
        self.client = FakeSheetClient()
        self.tools = SheetTools(client=self.client)

    def test_col_letter(self):
        self.assertEqual([col_letter(i) for i in (0, 25, 26, 27)], ["A", "Z", "AA", "AB"])

    def test_get_structure_shape(self):
        s = self.tools.get_sheet_structure()
        self.assertEqual(s["headers"], HEADERS)
        self.assertEqual(s["num_rows"], 3)
        self.assertEqual(s["num_cols"], 5)
        self.assertEqual(s["sheet_id"], 999)
        self.assertIn("Application Status", s["validations"])

    def test_append_row_maps_names_to_positions(self):
        out = self.tools.append_row({"Role": "QA Tester", "Company Name": "Rapta"})
        self.assertEqual(out["row_index"], 5)  # 1 header + 3 data + 1
        # Values land under the right columns regardless of dict order.
        self.assertEqual(out["values"], ["Rapta", "", "QA Tester", "", ""])
        self.assertEqual(self.client.updates[-1][0], "A5:E5")

    def test_append_row_rejects_unknown_column(self):
        with self.assertRaises(ValueError):
            self.tools.append_row({"Nope": "x"})

    def test_add_column_uses_zero_based_insert_and_writes_header(self):
        self.tools.add_column("Date Applied", position=4)  # column D
        req = self.client.batch_requests[0][0]["insertDimension"]
        self.assertEqual(req["range"]["startIndex"], 3)
        self.assertEqual(req["range"]["endIndex"], 4)
        self.assertEqual(self.client.updates[-1], ("D1", [["Date Applied"]]))

    def test_add_column_with_validation_applies_dropdown(self):
        self.tools.add_column("Stage", position=2, validation_options=["a", "b"])
        kinds = [list(r[0].keys())[0] for r in self.client.batch_requests]
        self.assertIn("insertDimension", kinds)
        self.assertIn("setDataValidation", kinds)

    def test_remove_column_requires_confirmation(self):
        out = self.tools.remove_column("Salary")
        self.assertTrue(out["needs_confirmation"])
        self.assertEqual(out["value_count"], 2)  # Acme 100k, Gamma 90k
        self.assertEqual(self.client.batch_requests, [])  # nothing deleted

    def test_remove_column_confirmed_deletes_right_index(self):
        out = self.tools.remove_column("Salary", confirmed=True)
        req = self.client.batch_requests[0][0]["deleteDimension"]
        self.assertEqual(req["range"]["startIndex"], 3)  # Salary is column D
        self.assertEqual(out["deleted_values"], 2)

    def test_rename_column_writes_header_cell(self):
        self.tools.rename_column("Salary", "Comp")
        self.assertEqual(self.client.updates[-1], ("D1", [["Comp"]]))

    def test_set_validation_remaps_invalid_statuses(self):
        out = self.tools.set_column_validation("Application Status", CANONICAL)
        self.assertEqual(out["invalid_values_found"], 2)  # two N/A cells
        self.assertEqual(out["reconciled"], 2)
        self.assertEqual(out["remap_target"], "Not started")
        # Remap writes went to B2 and B4.
        ranges = [u["range"] for u in self.client.ws.batch_updates[-1]]
        self.assertEqual(sorted(ranges), ["B2", "B4"])

    def test_clean_placeholders_default_rules_audit(self):
        out = self.tools.clean_placeholders()
        self.assertEqual(out["audit"]["Application Status"]["changed"], 2)
        self.assertEqual(out["audit"]["Rejection Reason"]["changed"], 1)
        self.assertEqual(out["total_changed"], 3)


if __name__ == "__main__":
    unittest.main(verbosity=2)
