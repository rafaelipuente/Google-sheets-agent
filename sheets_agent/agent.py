"""Agent loop: turns prompts into tool calls (Method C chat interface).

The model never touches the sheet. It emits tool calls; the tool layer acts.
"""

from __future__ import annotations

import json

from . import config
from .models import OpenAIModel, ToolCallingModel
from .observability import UsageTracker, timer
from .schemas import TOOL_SCHEMAS
from .tools import SheetTools

STRUCTURAL_TOOLS = {"add_column", "remove_column", "rename_column", "set_column_validation"}
DESTRUCTIVE_TOOLS = {"remove_column", "delete_row"}
# Tools whose effect shifts indices/row counts; re-read structure afterwards.
REREAD_AFTER = STRUCTURAL_TOOLS | {"delete_row"}

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

    def _structure_message(self) -> dict:
        # The forced structure read is a system step, not a model tool call.
        with timer() as t:
            structure = self.tools.get_sheet_structure()
        self.tracker.record_system_step("get_sheet_structure", t.ms)
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

    def send(self, prompt: str) -> str:
        """Run one user turn through the loop; return the assistant's reply."""
        self.messages.append(self._structure_message())
        self.messages.append({"role": "user", "content": prompt})

        while True:
            with timer() as t:
                response = self.model.complete(self.messages, TOOL_SCHEMAS)
            self.tracker.record_llm_call(
                model=response.model,
                usage=response.usage,
                latency_ms=response.latency_ms or t.ms,
                num_tool_calls=len(response.tool_calls),
            )
            self.messages.append(response.assistant_message)

            if not response.tool_calls:
                link = config.sheet_url(self.tools.client.sheet_id)
                return f"{response.content}\n\nSheet: {link}"

            did_structural = False
            for call in response.tool_calls:
                name = call.name
                args = json.loads(call.arguments or "{}")
                with timer() as tt:
                    try:
                        result = self._dispatch(name, args)
                        ok, err = True, None
                    except Exception as exc:  # surface tool errors back to the model
                        result = {"error": str(exc)}
                        ok, err = False, str(exc)
                self.tracker.record_tool_call(name, tt.ms, ok, err)
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
                self.messages.append(self._structure_message())
