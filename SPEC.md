# SPEC: Sheets Agent (Job Application Tracker)

A natural-language agent that reads, writes, and reshapes a Google Sheets job-application tracker from prompts. The model decides what to change. Your code performs the changes through the Google Sheets API.

## 1. Goal

Type a prompt like "add Rapta, QA Tester, status applied" and have the agent append the row to the real sheet. Type "add a column for the date I applied" and have it insert the column with a sensible header. The real Google Sheet stays the source of truth. You click through to it to eyeball results.

First user: you, tracking your own applications. Build it usable this week, then grow it into a portfolio piece.

## 2. Scope

In scope for v1:
- Read sheet structure and values
- Append and update rows (data operations)
- Add, remove, rename columns and manage dropdown validation (schema operations)
- A chat loop that turns prompts into tool calls and posts a result/diff back

Out of scope for v1:
- Multi-user OAuth (service account only to start)
- Embedded editable grid UI (see UI section for why)
- Multiple sheets/tabs (single tab: "Tracking Template")
- Undo/version history beyond what Google itself stores

## 3. Architecture

```
prompt --> agent loop --> model emits tool call (JSON)
                              |
                              v
                       your tool layer (Python)
                              |
                  +-----------+-----------+
                  |                       |
          gspread (values)        raw Sheets API (structural)
                  |                       |
                  +-----------+-----------+
                              v
                     real Google Sheet
```

Key principle: the model never touches the sheet. It emits tool calls. Your code calls the API. The model is fully decoupled and gets picked at the end, not now. Build v1 on whatever tool-calling model you already have wired up; swap later if you want.

## 4. The target sheet

Spreadsheet ID (from the URL): `1BqY1E0Qcnrmo8qgRRSEE7TK3fy5TXWFLc2rYIdOrqDs`
(Verify against your URL bar. The 16th-from-last character is an uppercase `I`, easy to confuse with a lowercase `l`.)
Tab: `Tracking Template`

Starting schema (row 1 is headers):

| Column | Header | Type |
|--------|--------|------|
| A | Company Name | text |
| B | Application Status | dropdown (ONE_OF_LIST) |
| C | Role | text |
| D | Salary | text/number |
| E | Date Submitted | date/text |
| F | Link to Job Req | text/URL |
| G | Rejection Reason | text |
| H | Notes | text |

The Application Status dropdown is a data validation rule, not plain text. This matters for every schema operation (see Risks).

### 4.1 Canonical Application Status values

The dropdown in column B should offer exactly these options, in this order:

1. Not started
2. To apply
3. Applied
4. OA received
5. Interviewing
6. Offer
7. Rejected
8. Ghosted (30+ days, no response)

This list is the single source of truth for the Status dropdown. "Standardize my statuses" maps to `set_column_validation(column="Application Status", options=[...this list...])`. No tool change needed; the capability already exists.

**Status migration is decided, not open.** When "standardize statuses" runs, the operation must, as a single atomic flow:

1. Remap every existing `N/A` in the Application Status column to `Not started`.
2. Apply the canonical dropdown (the eight options above, in order).

This is the chosen behavior. It is not a "clear vs remap vs report" choice anymore: it is remap-to-`Not started`. The remap is part of the same `set_column_validation` call (its default reconcile behavior), never a separate manual step the user has to remember.

### 4.2 Notes column convention

The Notes column (H) is free text, but the agent should follow a convention when writing to it: capture who you talked to, what you customized for that application, and any follow-up dates. This is prompt/content guidance, not a separate tool.

## 5. Authentication

Service account, v1.

1. Create a service account in Google Cloud Console.
2. Enable the Google Sheets API for the project.
3. Download the JSON key.
4. Share the target sheet with the service account's email (the `client_email` in the JSON), with Editor access, exactly as you'd share with a person.

No OAuth consent flow needed because there is one user and the sheet is shared directly. Store the JSON key path in an env var (`GOOGLE_SERVICE_ACCOUNT_JSON`), never commit it.

When this grows to other users, this becomes OAuth2 with per-user consent and token storage. Explicitly deferred.

## 6. UI: Method C (chat + click-through)

Decision: Method C for v1.

Flow:
```
prompt in --> agent reads structure --> agent calls tools -->
result/diff posts back in chat --> user clicks link to the Google Sheet
```

No grid rendering. No embedded sheet. The chat shows a confirmation or a diff line ("Appended row 24: Rapta / Applied / QA Tester / N/A"). The user opens the real sheet to see it rendered.

Why not embed the live sheet: Google blocks framing of the Sheets editor. Published-to-web embeds are read-only. So an editable Google grid inside your own app is not on the table. Method C sidesteps this entirely.

### 6.1 The v1 interface: a local web chat page

Method C is delivered as a minimal local web app, which is how the tool is actually run now.

- **FastAPI app.** Serves a single static HTML chat page at `/`: a scrollable message list with a text input at the bottom. Plain HTML/CSS/JS, no framework.
- **`POST /chat`.** Takes `{ "message": "..." }`, passes the message straight into the existing agent loop (the one that forces `get_sheet_structure`, lets the model chain tool calls, and returns the diff/confirmation text plus the sheet link), and returns `{ "reply": "...", "sheet_url": "..." }`. It reuses the agent code; it does not reimplement agent logic.
- **Backend-held conversation state.** A single persistent agent instance keeps the message history server-side, so multi-turn flows work. In particular, the confirm-before-delete handshake survives across requests: the agent's "confirm?" reply shows in chat, and the user's next typed "yes"/"no" resolves it. Nothing auto-confirms.
- **Run it.** `uvicorn sheets_agent.web:app --reload`, then open `http://localhost:8000`.

"Next to my sheet" means two browser windows snapped side by side: the chat page in one, the real Google Sheet tab in the other. It is not an embedded sheet, for the framing reason above (Google blocks framing the live editable editor). The chat posts diffs/confirmations and a click-through link; the user eyeballs the rendered result in the adjacent Sheets window.

Later (not v1): Method B renders the rows yourself in a grid component (AG Grid / Handsontable) for split-pane polish. Method A is a browser extension sidebar for an in-Sheets feel. Both deferred.

## 7. Tool layer

Two tiers. Eight tools.

### 7.1 Data tier

```python
get_sheet_structure() -> {
    "headers": list[str],          # ["Company Name", "Application Status", "Role", "Salary"]
    "num_rows": int,
    "num_cols": int,
    "sheet_id": int,               # the numeric gid, NOT the tab name
    "sample_rows": list[list[str]],# first ~5 data rows so the model sees real values
    "validations": dict             # {col_name: [options]}, so model knows Status is a dropdown
}

read_range(a1: str) -> list[list[str]]

update_cells(a1: str, values: list[list]) -> {"updated_cells": int}

append_row(row: dict) -> {"row_index": int}
    # row keyed by header NAME, e.g. {"Company Name": "Rapta", "Role": "QA Tester"}
    # tool maps names to current column positions internally
```

### 7.2 Schema tier

```python
add_column(name: str, position: int, validation_options: list[str] = None) -> {...}

remove_column(name: str, confirmed: bool = False) -> {...}
    # refuses unless confirmed=True

rename_column(old: str, new: str) -> {...}

set_column_validation(column: str, options: list[str]) -> {...}
    # for the Application Status column this also remaps existing N/A cells to
    # "Not started" in the same call (see 4.1); the remap is the default
    # reconcile behavior, not a separate step.
```

### 7.3 Two rules that are the actual crux

**Name-to-index resolution lives in the tool layer, never the model.** The model says "remove Salary." Your `remove_column` reads the current header row, finds Salary at index 3, acts. The model never tracks column positions. It will drift if you make it.

**`get_sheet_structure` runs after every structural change, not just at the start.** Adding or removing a column shifts every index to its right. The agent must re-read structure before its next action or it writes to the wrong column.

## 8. Two APIs, one bridge

Value operations use the `spreadsheets.values` endpoint with A1 notation (gspread wraps this cleanly).

Structural operations use `spreadsheets.batchUpdate` with the numeric `sheet_id` and zero-based dimension indices. gspread does not wrap all of this cleanly, so drop to the raw API.

`sheet_id` is the bridge between them. That's why `get_sheet_structure` returns it.

### 8.1 batchUpdate request bodies

Add a column at zero-based index 4 (column E):
```python
{"requests": [{"insertDimension": {
    "range": {"sheetId": sheet_id, "dimension": "COLUMNS",
              "startIndex": 4, "endIndex": 5},
    "inheritFromBefore": True}}]}
```

Remove column D (zero-based index 3):
```python
{"requests": [{"deleteDimension": {
    "range": {"sheetId": sheet_id, "dimension": "COLUMNS",
              "startIndex": 3, "endIndex": 4}}}]}
```

Recreate a dropdown (whenever you add a status-like column or shift the existing one):
```python
{"requests": [{"setDataValidation": {
    "range": {"sheetId": sheet_id, "startRowIndex": 1,
              "startColumnIndex": col_idx, "endColumnIndex": col_idx + 1},
    "rule": {"condition": {"type": "ONE_OF_LIST",
                           "values": [{"userEnteredValue": o} for o in options]},
             "showCustomUi": True}}}]}
```

## 9. Safety: confirm before delete

Destructive operations (column delete, and any future row delete) are gated behind a confirmation. The agent states the plan and waits.

Example: user says "remove salary" -> agent responds "I'll delete column D 'Salary' and its 23 values. Confirm?" -> only on yes does `remove_column(name="Salary", confirmed=True)` run.

Non-destructive operations (append row, add column, rename, update cells) run automatically and show a diff after.

Rationale: the Sheets API has no native undo. Adding a row is cheap to reverse. Deleting a populated column is not.

Ambiguity to handle: "remove salary" could mean delete the column or just clear its values. The agent should state which one it's about to do in the confirmation, so you catch a wrong read before it executes.

## 10. Agent loop

```
1. Receive prompt.
2. ALWAYS call get_sheet_structure first. Inject result into model context.
3. Model reasons and emits one or more tool calls.
4. For each tool call:
   a. If destructive and not yet confirmed: post the plan to chat, stop, wait for user yes/no.
   b. Otherwise execute, capture result.
5. If a structural change ran, call get_sheet_structure again before any further tool call.
6. Post a diff/confirmation line to chat.
7. Provide the click-through link to the sheet.
```

The forced `get_sheet_structure` at step 2 is what gives the model situational awareness without dumping the whole sheet into context. Read targeted ranges, never whole tabs, to protect the context budget.

This forced read is a *system step*, not a model-emitted tool call. Observability must label it as such (see Observability) so a turn that did real work but emitted no model tool calls still reads as "0 model tool calls, 1 system step" rather than a misleading bare "0 tool calls".

## 11. Build and test order

Mirror the data-plumbing-first discipline. Do not build the agent loop until all eight tools work when called by hand.

1. **Plumbing.** Service account reads and writes a single cell from a script. The "is it connected" milestone. Do this in isolation.
2. **Data tier.** Write the four data tools as plain Python. Call each by hand. Confirm `append_row` maps header names to the right columns and `get_sheet_structure` reports the Status dropdown under `validations`.
3. **Schema tier.** Write the four schema tools. Test `add_column` and `remove_column` against a throwaway COPY of the template. Do NOT test deletes on your real tracker. Confirm the dropdown survives a column insert to its left (this is the main thing that breaks).
4. **Agent loop.** Bolt the model on top. Now failures are reasoning failures, because the tools are already proven.

## 12. Acceptance criteria

v1 is done when, against a copy of the real sheet:

- [ ] Service account reads and writes a single cell.
- [ ] `get_sheet_structure` returns correct headers, sheet_id, sample rows, and reports Application Status as a dropdown with its options.
- [ ] "Add Rapta, QA Tester, applied" appends a correct row with Status set via the dropdown values.
- [ ] "Add a column for date applied after Role" inserts the column at the right position with that header, and the Status dropdown still works.
- [ ] "Standardize my statuses" remaps every existing `N/A` in the Application Status column to `Not started` and then applies the canonical dropdown (exactly the eight values in order), as one operation. It reports how many cells were remapped.
- [ ] "Rename Salary to Comp" renames in place without data loss.
- [ ] "Remove the Salary column" triggers a confirmation stating the column and its value count, and only deletes on yes.
- [ ] After any structural change, the next data write lands in the correct column (proves the re-read-structure step works).
- [ ] Every action posts a diff/confirmation line and a link to the sheet in chat.

## 13. Risks and gotchas

- **Dropdown validation is fragile.** Inserting, removing, or reordering columns can blow away or misplace the Application Status validation. Schema tools that move columns must re-apply validation via `setDataValidation`. Test this explicitly.
- **Status migration (decided: remap to "Not started").** The current sheet has `N/A` in every status cell. The canonical list (section 4.1) does not include `N/A`, so applying the new dropdown would otherwise leave every existing cell holding a value the rule rejects (Sheets flags these, it does not auto-clear them). The decided behavior is: when the agent standardizes statuses, it **remaps every existing `N/A` to `Not started`** and reports the count, as part of the same `set_column_validation` call. This is not an open choice; it is the rule. The remap happens in the same operation as applying the dropdown, never as a separate manual step.
- **Index drift.** A1 notation and column indices are positional. Re-read structure after every structural change.
- **No native undo.** Confirm-before-delete is the safety net. Test deletes only on throwaway copies.
- **Context budget.** Large sheets blow the model's context if you dump whole tabs. Read targeted ranges only.
- **Two APIs.** Mixing `values` and `batchUpdate` is the main source of confusion. Keep value ops in gspread and structural ops in the raw API, bridged by `sheet_id`.
- **Ambiguous prompts.** "Clean up column C," "remove salary." The agent should state its interpretation before destructive or structural acts.

## 14. Stack

- Language: Python
- Sheets value ops: `gspread`
- Sheets structural ops: raw Google Sheets API (`google-api-python-client`)
- Auth: service account JSON key, path in `GOOGLE_SERVICE_ACCOUNT_JSON`
- Model: deferred, any reliable tool-calling model, picked after the tools work
- Interface: chat (Method C)

## 15. Observability

Every agent turn records, to a JSONL usage log:
- Each LLM call: model, prompt/completion/total tokens, computed cost, latency.
- Each model-emitted tool call: tool name, duration, success/error.
- Each forced `get_sheet_structure` read: labeled as a **system step**, distinct from model-emitted tool calls (see section 10). This keeps the log honest: a turn that read structure but emitted no model tool calls reads as "0 model tool calls, 1 system step", not a bare "0 tool calls".

The `usage` CLI view aggregates these into totals and per-model / per-tool / per-system-step breakdowns so cost and activity are auditable across runs.

## 16. Deferred (v2+)

- Schema editing is already in v1. The deferred items are: multi-user OAuth, Method B split-pane grid, Method A browser extension, multiple tabs, richer diff previews, and code-generation tooling (model writes Python against the API in a sandbox) for complex one-shot reshapes.
