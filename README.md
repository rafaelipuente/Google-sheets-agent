# Sheets Agent (Job Application Tracker)

A natural-language agent that reads, writes, and reshapes a Google Sheets
job-application tracker from prompts. The model decides what to change; this
code performs the changes through the Google Sheets API. The model never touches
the sheet directly.

## Architecture

```
prompt -> agent loop -> model emits tool call (JSON)
                              |
                       tool layer (Python)
                     /                      \
        gspread (values)            raw Sheets API (structural)
                     \                      /
                       real Google Sheet
```

The numeric `sheet_id` (gid) bridges the two APIs. Value ops use `gspread`;
structural ops use `spreadsheets.batchUpdate` via `google-api-python-client`.

## Setup

1. Create a project in Google Cloud Console and enable the Google Sheets API.
2. Create a service account and download its JSON key.
3. Copy `.env.example` to `.env` and fill in:
   - `GOOGLE_SERVICE_ACCOUNT_JSON` - absolute path to the key file
   - `SPREADSHEET_ID` - from the sheet URL (the 16th-from-last char is uppercase `I`)
   - `WORKSHEET_NAME` - defaults to `Tracking Template`
   - `OPENAI_API_KEY` and `OPENAI_MODEL` (defaults to `gpt-4o`)
4. Install deps:

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
```

5. Find the service account email and share the sheet (Editor) with it:

```bash
python -m sheets_agent.cli whoami
```

> Test schema/delete operations against a COPY of the sheet, never your real
> tracker. The Sheets API has no native undo.

## The eight tools (plus a cleanup primitive)

Data tier: `get_sheet_structure`, `read_range`, `update_cells`, `append_row`,
`update_entry` (edit an existing entry by Company Name), `delete_row`
(confirm-gated). Schema tier: `add_column`, `remove_column` (confirm-gated),
`rename_column`, `set_column_validation`. Plus `clean_placeholders` for
trustworthy, audited placeholder normalization. Every call is metered (see
Observability below).

Two rules: name-to-index resolution lives in the tool layer (never the model),
and the agent re-reads structure after every structural change so writes never
drift to the wrong column.

## Hand-test sequence (build order, run against a COPY)

```bash
python -m sheets_agent.cli ping
python -m sheets_agent.cli structure
python -m sheets_agent.cli call append_row '{"row": {"Company Name": "Rapta", "Role": "QA Tester", "Application Status": "Applied"}}'
python -m sheets_agent.cli call add_column '{"name": "Date Applied", "position": 4}'
python -m sheets_agent.cli call set_column_validation '{"column": "Application Status", "options": ["Not started","To apply","Applied","OA received","Interviewing","Offer","Rejected","Ghosted (30+ days, no response)"]}'
python -m sheets_agent.cli call clean_placeholders '{}'
python -m sheets_agent.cli call rename_column '{"old": "Salary", "new": "Comp"}'
python -m sheets_agent.cli call remove_column '{"name": "Comp"}'           # returns needs_confirmation
python -m sheets_agent.cli call remove_column '{"name": "Comp", "confirmed": true}'
```

## Web UI

A minimal one-page chat to run beside your Google Sheet tab:

```bash
uvicorn sheets_agent.web:app --reload
```

Then open http://localhost:8000. Conversation state is kept on the backend, so
confirming a delete with a follow-up "yes" works.

## Chat loop

```bash
python -m sheets_agent.cli chat
```

Example prompts: "add Rapta, QA Tester, status applied", "add a column for the
date I applied after Role", "standardize my statuses", "clean up my tracker",
"rename Salary to Comp", "remove the Salary column".

## Status standardization and placeholder cleanup

- "Standardize my statuses" applies the eight canonical options and remaps any
  stale value (e.g. `N/A`) to `Not started`, reporting the count.
- "Clean up my tracker" runs `clean_placeholders`: remaps Application Status
  `N/A` to `Not started`, clears Rejection Reason `N/A`, and returns an audit of
  exactly how many cells changed per column.

## Observability (cost, LLM calls, tool usage)

Every agent turn records its LLM calls (model, token usage, cost, latency) and
each tool call (duration, success/error) to a JSONL log
(`.sheets_agent/usage.jsonl` by default, override with `USAGE_LOG_PATH`). Cost is
computed from a per-model pricing table in `sheets_agent/observability.py`.

View the rolling totals and per-model / per-tool breakdown:

```bash
python -m sheets_agent.cli usage          # formatted view
python -m sheets_agent.cli usage --json   # raw summary for scripts/dashboards
```

## Tests

```bash
python -m unittest discover -s tests
```

Coverage: the tool layer (name-to-index resolution, zero-based structural
indices, dropdown re-apply, audited cleanup counts) via a fake sheet client; the
agent loop (forced structure read, confirm-before-delete guard) via a fake
model; the model adapter mapping; and observability cost/summary math. None of
the tests touch Google or the network.

## Acceptance checklist (v1)

- [ ] Service account reads and writes a single cell (`ping`).
- [ ] `get_sheet_structure` returns headers, sheet_id, sample rows, and reports
      Application Status as a dropdown with its options.
- [ ] "Add Rapta, QA Tester, applied" appends a correct row.
- [ ] "Add a column for date applied after Role" inserts at the right position
      and the Status dropdown still works.
- [ ] "Standardize my statuses" sets the eight canonical values and reconciles
      `N/A` (remap to `Not started`) with a reported count.
- [ ] "Rename Salary to Comp" renames in place without data loss.
- [ ] "Remove the Salary column" confirms with the value count, deletes on yes.
- [ ] After a structural change, the next write lands in the correct column.
- [ ] Every action posts a diff/confirmation line and a sheet link.

## Deferred (v2+)

Multi-user OAuth, split-pane grid (Method B), browser extension (Method A),
multiple tabs, richer diff previews, code-generation tooling.
