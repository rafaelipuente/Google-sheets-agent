"""Service account authentication (v1: single user, JSON key)."""

from __future__ import annotations

import os

import gspread
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build

from . import config


def _load_credentials() -> Credentials:
    path = config.SERVICE_ACCOUNT_JSON
    if not path:
        raise RuntimeError(
            "GOOGLE_SERVICE_ACCOUNT_JSON is not set. Point it at your service "
            "account key file (see .env.example)."
        )
    if not os.path.exists(path):
        raise RuntimeError(f"Service account key not found at: {path}")
    return Credentials.from_service_account_file(path, scopes=config.GOOGLE_SCOPES)


def build_clients():
    """Return (gspread_client, raw_sheets_service) sharing one credential.

    gspread handles value ops; the raw service handles structural batchUpdate.
    """
    creds = _load_credentials()
    gc = gspread.authorize(creds)
    service = build("sheets", "v4", credentials=creds, cache_discovery=False)
    return gc, service


def service_account_email() -> str:
    """The client_email to share the sheet with (Editor access)."""
    return _load_credentials().service_account_email
