"""Agent loop: turns prompts into tool calls (Method C chat interface).

The model never touches the sheet. It emits tool calls; the tool layer acts.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime

from . import config
from .models import OpenAIModel, ToolCallingModel
from .observability import UsageTracker, timer
from .retry import is_connection_error
from .schemas import TOOL_SCHEMAS
from .tools import SheetTools

log = logging.getLogger("sheets_agent")

STRUCTURAL_TOOLS = {"add_column", "remove_column", "rename_column", "set_column_validation"}
DESTRUCTIVE_TOOLS = {"remove_column", "delete_row"}
# Tools whose effect shifts indices/row counts; re-read structure afterwards.
REREAD_AFTER = STRUCTURAL_TOOLS | {"delete_row"}
# Row writes that set field values; their success gets a deterministic, full-field
# confirmation instead of the model's free-text diff.
FIELD_WRITE_TOOLS = {"append_row", "update_entry"}
# Multi-call schema tools: a mid-flight failure may leave a partial change.
MULTI_STEP_WRITES = {"add_column", "set_column_validation"}


class AgentError(Exception):
    """A user-facing failure with a plainspoken message (no stack trace)."""

    def __init__(self, user_message: str) -> None:
        super().__init__(user_message)
        self.user_message = user_message

SYSTEM_PROMPT = f"""You are a job-application tracker assistant operating on a single \
Google Sheet tab. You change the sheet only by emitting tool calls; your code \
performs the changes.

Rules:
- The current sheet structure is injected before each user turn. Trust it for \
column names and positions. Never guess column indices yourself.
- After any structural change, refreshed structure is injected again before you \
continue. Re-read it.
- Refer to columns by NAME. The tool layer maps names to positions.
- Confirm before destructive acts. To delete a column, first call remove_column \
WITHOUT confirmed to fetch its value count, state the plan to the user \
("I'll delete column D 'Salary' and its N values. Confirm?"), and only call \
remove_column with confirmed=true after the user explicitly says yes.
- The same confirm-first rule applies to delete_row: call it WITHOUT confirmed \
to get a preview of the row, show it, and only call with confirmed=true after \
an explicit yes.
- To change an existing application (e.g. "set Google's status to Offer", \
"update Rapta's salary"), use update_entry with the company name and only the \
fields that change. Do not append a new row for an edit. If a company matches \
several rows, ask the user which row_index to use.
- For ambiguous prompts ("remove salary", "clean up column C"), state your \
interpretation (delete the column vs clear its values) before acting.
- The canonical Application Status options, in order, are: \
{config.CANONICAL_STATUS_OPTIONS}. "Standardize my statuses" means \
set_column_validation on "{config.STATUS_COLUMN}" with exactly these.
- When writing the Notes column, follow the convention: who you talked to, what \
you customized for the application, and any follow-up dates.
- "Clean up my tracker" / "clean placeholders" maps to clean_placeholders, which \
remaps Application Status "N/A" to "Not started" and clears Rejection Reason \
"N/A". Trust the counts it returns.
- After acting, reply with a short diff line (e.g. "Appended row 24: Rapta / \
QA Tester / Applied"). When an action changed multiple cells (standardize, \
cleanup), report an audit summary using the exact counts from the tool results \
(e.g. "Remapped 23 statuses to Not started; cleared 5 Rejection Reason cells"). \
Never invent counts; only use what the tools returned.
"""


class Agent:
    def __init__(
        self,
        tools: SheetTools | None = None,
        model: ToolCallingModel | None = None,
        tracker: UsageTracker | None = None,
    ) -> None:
        self.tools = tools or SheetTools()
        self.model = model or OpenAIModel()
        self.tracker = tracker or UsageTracker()
        self.messages: list[dict] = [{"role": "system", "content": SYSTEM_PROMPT}]
        self._confirmed_tokens: set[str] = set()
        self._last_headers: list[str] = []

    def _structure_message(self) -> dict:
        # The forced structure read is a system step, not a model tool call.
        with timer() as t:
            structure = self.tools.get_sheet_structure()
        self.tracker.record_system_step("get_sheet_structure", t.ms)
        self._last_headers = structure.get("headers", [])
        return {
            "role": "system",
            "content": "Current sheet structure:\n" + json.dumps(structure, indent=2),
        }

    @staticmethod
    def _confirm_token(name: str, args: dict) -> str:
        if name == "remove_column":
            return f"col:{str(args.get('name', '')).strip().lower()}"
        if name == "delete_row":
            key = args.get("row_index")
            if key is None:
                key = str(args.get("company", "")).strip().lower()
            return f"row:{key}"
        return name

    def _dispatch(self, name: str, args: dict):
        # Safety guard: never honor a confirmed delete the user never approved.
        if name in DESTRUCTIVE_TOOLS and args.get("confirmed"):
            if self._confirm_token(name, args) not in self._confirmed_tokens:
                args = {**args, "confirmed": False}
        result = getattr(self.tools, name)(**args)
        if name in DESTRUCTIVE_TOOLS and result.get("needs_confirmation"):
            self._confirmed_tokens.add(self._confirm_token(name, args))
        return result

    def _link(self) -> str:
        return config.sheet_url(self.tools.client.sheet_id)

    def _fail(self, service: str, step: str, saved: str, exc: Exception) -> AgentError:
        """Log the full traceback and build a plainspoken chat message."""
        log.exception("Failure while %s: %s", step, exc)
        if saved == "partial":
            tail = "The change may have been partially applied — open the sheet to check."
        elif service == "model service":
            tail = "No changes were saved — retry."
        else:
            tail = "No changes were saved — retry, or open the sheet to check."
        return AgentError(f"Couldn't reach the {service} while {step}. {tail}")

    def _format_write_confirmation(self, tool: str, result: dict) -> str:
        """Echo every populated field plus a source-of-truth marker."""
        values = result.get("values", [])
        fields = [
            f"{header}: {value}"
            for header, value in zip(self._last_headers, values)
            if str(value).strip()
        ]
        verb = "Added" if tool == "append_row" else "Updated"
        row = result.get("row_index")
        stamp = datetime.now().strftime("%-I:%M %p")
        marker = (
            f"Updated in Google Sheets · {config.WORKSHEET_NAME} · row {row} · {stamp}"
        )
        body = "\n".join(fields) if fields else "(no field values)"
        return f"{verb} row {row} in {config.WORKSHEET_NAME}:\n{body}\n\n{marker}"

    def send(self, prompt: str) -> str:
        """Run one user turn through the loop; return the assistant's reply."""
        baseline = len(self.messages)  # roll back to here if the turn fails
        try:
            return self._run_turn(prompt)
        except AgentError:
            del self.messages[baseline:]  # don't leave a half-finished turn in history
            raise

    def _run_turn(self, prompt: str) -> str:
        try:
            structure_msg = self._structure_message()
        except Exception as exc:
            raise self._fail("sheet service", "reading the tracker structure", "none", exc)
        self.messages.append(structure_msg)
        self.messages.append({"role": "user", "content": prompt})

        last_write: tuple[str, dict] | None = None

        while True:
            try:
                with timer() as t:
                    response = self.model.complete(self.messages, TOOL_SCHEMAS)
            except Exception as exc:
                raise self._fail("model service", "working out your request", "none", exc)
            self.tracker.record_llm_call(
                model=response.model,
                usage=response.usage,
                latency_ms=response.latency_ms or t.ms,
                num_tool_calls=len(response.tool_calls),
            )
            self.messages.append(response.assistant_message)

            if not response.tool_calls:
                if last_write is not None:
                    reply = self._format_write_confirmation(*last_write)
                else:
                    reply = response.content or ""
                return f"{reply}\n\nSheet: {self._link()}"

            did_structural = False
            for call in response.tool_calls:
                name = call.name
                args = json.loads(call.arguments or "{}")
                with timer() as tt:
                    try:
                        result = self._dispatch(name, args)
                        ok, err = True, None
                    except Exception as exc:
                        # Network drops while touching the sheet get a clean message;
                        # logic errors still go back to the model to recover.
                        if is_connection_error(exc):
                            self.tracker.record_tool_call(name, tt.ms, False, str(exc))
                            saved = "partial" if name in MULTI_STEP_WRITES else "none"
                            raise self._fail(
                                "sheet service", f"running {name}", saved, exc
                            )
                        result = {"error": str(exc)}
                        ok, err = False, str(exc)
                self.tracker.record_tool_call(name, tt.ms, ok, err)
                if (
                    ok
                    and name in FIELD_WRITE_TOOLS
                    and result.get("row_index")
                    and not result.get("error")
                ):
                    last_write = (name, result)
                self.messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": call.id,
                        "content": json.dumps(result, default=str),
                    }
                )
                if name in REREAD_AFTER and not result.get("needs_confirmation") and not result.get("error"):
                    did_structural = True

            if did_structural:
                try:
                    self.messages.append(self._structure_message())
                except Exception as exc:
                    raise self._fail(
                        "sheet service", "re-reading the tracker structure", "none", exc
                    )
