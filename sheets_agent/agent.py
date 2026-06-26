"""Agent loop: turns prompts into tool calls (Method C chat interface).

The model never touches the sheet. It emits tool calls; the tool layer acts.
"""

from __future__ import annotations

import json

from . import config
from .models import OpenAIModel, ToolCallingModel
from .schemas import TOOL_SCHEMAS
from .tools import SheetTools

STRUCTURAL_TOOLS = {"add_column", "remove_column", "rename_column", "set_column_validation"}

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
    ) -> None:
        self.tools = tools or SheetTools()
        self.model = model or OpenAIModel()
        self.messages: list[dict] = [{"role": "system", "content": SYSTEM_PROMPT}]
        self._confirmed_columns: set[str] = set()

    def _structure_message(self) -> dict:
        structure = self.tools.get_sheet_structure()
        return {
            "role": "system",
            "content": "Current sheet structure:\n" + json.dumps(structure, indent=2),
        }

    def _dispatch(self, name: str, args: dict):
        # Safety guard: never honor a confirmed delete the user never approved.
        if name == "remove_column" and args.get("confirmed"):
            if args.get("name") not in self._confirmed_columns:
                args = {**args, "confirmed": False}
        result = getattr(self.tools, name)(**args)
        if name == "remove_column" and result.get("needs_confirmation"):
            self._confirmed_columns.add(args.get("name"))
        return result

    def send(self, prompt: str) -> str:
        """Run one user turn through the loop; return the assistant's reply."""
        self.messages.append(self._structure_message())
        self.messages.append({"role": "user", "content": prompt})

        while True:
            response = self.model.complete(self.messages, TOOL_SCHEMAS)
            self.messages.append(response.assistant_message)

            if not response.tool_calls:
                link = config.sheet_url(self.tools.client.sheet_id)
                return f"{response.content}\n\nSheet: {link}"

            did_structural = False
            for call in response.tool_calls:
                name = call.name
                args = json.loads(call.arguments or "{}")
                try:
                    result = self._dispatch(name, args)
                except Exception as exc:  # surface tool errors back to the model
                    result = {"error": str(exc)}
                self.messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": call.id,
                        "content": json.dumps(result, default=str),
                    }
                )
                if name in STRUCTURAL_TOOLS and not result.get("needs_confirmation") and not result.get("error"):
                    did_structural = True

            if did_structural:
                self.messages.append(self._structure_message())
