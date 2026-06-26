"""Configuration and canonical constants for the sheets agent."""

from __future__ import annotations

import os

from dotenv import load_dotenv

load_dotenv()


SPREADSHEET_ID = os.getenv(
    "SPREADSHEET_ID", "1BqY1E0Qcnrmo8qgRRSEE7TK3fy5TXWFLc2rYIdOrqDs"
)
WORKSHEET_NAME = os.getenv("WORKSHEET_NAME", "Tracking Template")
SERVICE_ACCOUNT_JSON = os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON")

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o")

# Section 4.1: the single source of truth for the Application Status dropdown.
CANONICAL_STATUS_OPTIONS = [
    "Not started",
    "To apply",
    "Applied",
    "OA received",
    "Interviewing",
    "Offer",
    "Rejected",
    "Ghosted (30+ days, no response)",
]

STATUS_COLUMN = "Application Status"

# Section 13 / placeholder cleanup: the current template seeds "N/A" everywhere.
# Convert status placeholders into a valid workflow state; clear free-text ones.
DEFAULT_PLACEHOLDER_RULES = [
    {"column": "Application Status", "match": "N/A", "action": "remap", "replacement": "Not started"},
    {"column": "Rejection Reason", "match": "N/A", "action": "clear", "replacement": ""},
]

# Scopes needed for both value ops (gspread) and structural ops (raw API).
GOOGLE_SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]


def sheet_url(sheet_gid: int | None = None) -> str:
    """Click-through link to the real Google Sheet (Method C)."""
    base = f"https://docs.google.com/spreadsheets/d/{SPREADSHEET_ID}/edit"
    if sheet_gid is not None:
        base += f"#gid={sheet_gid}"
    return base
