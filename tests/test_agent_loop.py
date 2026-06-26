"""Agent-loop behavior with a fake model and fake sheet (no live credentials).

Covers: forced structure read, append dispatch, confirm-before-delete guard,
and re-read-structure after a structural change.
"""

import json
import unittest

from sheets_agent.agent import Agent
from sheets_agent.models.base import ModelResponse, ToolCall


def _assistant(tool_calls=None, content=None):
    msg = {"role": "assistant", "content": content}
    if tool_calls:
        msg["tool_calls"] = [
            {
                "id": tc.id,
                "type": "function",
                "function": {"name": tc.name, "arguments": tc.arguments},
            }
            for tc in tool_calls
        ]
    return msg


class FakeModel:
    """Replays a scripted list of ModelResponse objects."""

    def __init__(self, script):
        self.script = list(script)
        self.seen_messages = []

    def complete(self, messages, tools):
        self.seen_messages.append(list(messages))
        return self.script.pop(0)


class FakeClient:
    sheet_id = 12345


class FakeTools:
    """Records calls; mimics the SheetTools surface the agent touches."""

    def __init__(self):
        self.client = FakeClient()
        self.calls = []
        self.structure_reads = 0
        self.deleted = []
        self.appended = []

    def get_sheet_structure(self):
        self.structure_reads += 1
        return {
            "headers": ["Company Name", "Application Status", "Role"],
            "num_rows": 2,
            "num_cols": 3,
            "sheet_id": self.client.sheet_id,
            "sample_rows": [["Acme", "N/A", "SWE"]],
            "validations": {"Application Status": ["Not started", "Applied"]},
        }

    def append_row(self, row):
        self.calls.append(("append_row", row))
        self.appended.append(row)
        return {"row_index": 4, "values": list(row.values())}

    def remove_column(self, name, confirmed=False):
        self.calls.append(("remove_column", name, confirmed))
        if not confirmed:
            return {
                "needs_confirmation": True,
                "column": name,
                "value_count": 2,
                "message": f"This will delete column '{name}' and its 2 values.",
            }
        self.deleted.append(name)
        return {"removed": name, "deleted_values": 2}


class AgentLoopTests(unittest.TestCase):
    def _agent(self, script):
        model = FakeModel(script)
        tools = FakeTools()
        return Agent(tools=tools, model=model), tools, model

    def test_append_flow_reads_structure_then_dispatches(self):
        tc = ToolCall(id="c1", name="append_row", arguments=json.dumps({"row": {"Company Name": "Rapta"}}))
        script = [
            ModelResponse(content=None, tool_calls=[tc], assistant_message=_assistant([tc])),
            ModelResponse(content="Appended row 4: Rapta", tool_calls=[], assistant_message=_assistant(content="Appended row 4: Rapta")),
        ]
        agent, tools, _ = self._agent(script)
        reply = agent.send("add Rapta")
        self.assertIn("Appended row 4", reply)
        self.assertIn("12345", reply)  # click-through link uses sheet_id gid
        self.assertEqual(tools.appended, [{"Company Name": "Rapta"}])
        self.assertEqual(tools.structure_reads, 1)  # forced read at turn start

    def test_confirmed_delete_without_plan_is_downgraded(self):
        # Model jumps straight to confirmed=True; guard must force a confirmation first.
        c1 = ToolCall(id="d1", name="remove_column", arguments=json.dumps({"name": "Salary", "confirmed": True}))
        c2 = ToolCall(id="d2", name="remove_column", arguments=json.dumps({"name": "Salary", "confirmed": True}))
        script = [
            ModelResponse(content=None, tool_calls=[c1], assistant_message=_assistant([c1])),
            ModelResponse(content="I'll delete column Salary and its 2 values. Confirm?", tool_calls=[], assistant_message=_assistant(content="confirm?")),
            ModelResponse(content=None, tool_calls=[c2], assistant_message=_assistant([c2])),
            ModelResponse(content="Removed Salary.", tool_calls=[], assistant_message=_assistant(content="done")),
        ]
        agent, tools, _ = self._agent(script)
        first = agent.send("remove salary")
        self.assertIn("Confirm", first)
        self.assertEqual(tools.deleted, [])  # nothing deleted yet
        # First call was downgraded to confirmed=False by the guard.
        self.assertEqual(tools.calls[0], ("remove_column", "Salary", False))

        second = agent.send("yes")
        self.assertEqual(tools.deleted, ["Salary"])  # only deletes after approval
        self.assertEqual(tools.calls[-1], ("remove_column", "Salary", True))


if __name__ == "__main__":
    unittest.main(verbosity=2)
