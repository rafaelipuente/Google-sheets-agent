"""OpenAI tool (function) schemas for the eight tools."""

from __future__ import annotations

TOOL_SCHEMAS = [
    {
        "type": "function",
        "function": {
            "name": "get_sheet_structure",
            "description": (
                "Read the current sheet structure: headers, row/col counts, the "
                "numeric sheet_id, sample data rows, and which columns are "
                "dropdowns. Call this before acting and again after any structural "
                "change."
            ),
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_range",
            "description": "Read a targeted A1 range. Never read whole tabs.",
            "parameters": {
                "type": "object",
                "properties": {"a1": {"type": "string", "description": "A1 range, e.g. 'A2:C10'"}},
                "required": ["a1"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "update_cells",
            "description": "Write values to an A1 range.",
            "parameters": {
                "type": "object",
                "properties": {
                    "a1": {"type": "string"},
                    "values": {
                        "type": "array",
                        "items": {"type": "array", "items": {}},
                        "description": "2D array of row values.",
                    },
                },
                "required": ["a1", "values"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "append_row",
            "description": (
                "Append a row keyed by header NAME (not position), e.g. "
                "{'Company Name': 'Rapta', 'Role': 'QA Tester', "
                "'Application Status': 'Applied'}."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "row": {
                        "type": "object",
                        "description": "Map of header name -> value.",
                        "additionalProperties": True,
                    }
                },
                "required": ["row"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "add_column",
            "description": (
                "Insert a new column at a 1-based position (A=1). Pass "
                "validation_options to make it a dropdown."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "position": {"type": "integer", "description": "1-based column position"},
                    "validation_options": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
                },
                "required": ["name", "position"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "remove_column",
            "description": (
                "Delete a column by NAME. Destructive: first call WITHOUT "
                "confirmed to get the plan and value count, present it to the "
                "user, then call again with confirmed=true only after explicit yes."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "confirmed": {"type": "boolean", "default": False},
                },
                "required": ["name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "rename_column",
            "description": "Rename a column header in place. No data loss.",
            "parameters": {
                "type": "object",
                "properties": {
                    "old": {"type": "string"},
                    "new": {"type": "string"},
                },
                "required": ["old", "new"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "clean_placeholders",
            "description": (
                "Normalize placeholder cells across columns and return an audit "
                "of counts. With no rules, applies the default: remap "
                "Application Status 'N/A' -> 'Not started' and clear Rejection "
                "Reason 'N/A'. Use for 'clean up my tracker'."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "rules": {
                        "type": "array",
                        "description": "Optional per-column rules; omit for defaults.",
                        "items": {
                            "type": "object",
                            "properties": {
                                "column": {"type": "string"},
                                "match": {"type": "string"},
                                "action": {"type": "string", "enum": ["remap", "clear"]},
                                "replacement": {"type": "string"},
                            },
                            "required": ["column"],
                        },
                    }
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "set_column_validation",
            "description": (
                "Set a column's dropdown (ONE_OF_LIST) options and reconcile "
                "existing stale values. reconcile is 'remap' (default), 'clear', "
                "or 'report'."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "column": {"type": "string"},
                    "options": {"type": "array", "items": {"type": "string"}},
                    "reconcile": {
                        "type": "string",
                        "enum": ["remap", "clear", "report"],
                        "default": "remap",
                    },
                },
                "required": ["column", "options"],
            },
        },
    },
]
