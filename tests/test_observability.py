"""Tests for the model adapter mapping and observability cost/summary."""

import os
import tempfile
import types
import unittest

from sheets_agent import observability
from sheets_agent.models.openai_model import OpenAIModel


def _fake_openai_response():
    func = types.SimpleNamespace(name="append_row", arguments='{"row": {}}')
    tool_call = types.SimpleNamespace(id="call_1", function=func)
    message = types.SimpleNamespace(
        content=None,
        tool_calls=[tool_call],
        model_dump=lambda exclude_none=True: {"role": "assistant", "content": None},
    )
    usage = types.SimpleNamespace(prompt_tokens=120, completion_tokens=30, total_tokens=150)
    choice = types.SimpleNamespace(message=message)
    return types.SimpleNamespace(choices=[choice], usage=usage)


class FakeOpenAIClient:
    def __init__(self):
        self.chat = types.SimpleNamespace(
            completions=types.SimpleNamespace(create=lambda **kw: _fake_openai_response())
        )


class ModelAdapterTests(unittest.TestCase):
    def test_complete_maps_tool_calls_and_usage(self):
        model = OpenAIModel(model="gpt-4o", api_key="x")
        model._client = FakeOpenAIClient()  # bypass real network client
        resp = model.complete(messages=[], tools=[])
        self.assertEqual(resp.model, "gpt-4o")
        self.assertEqual(len(resp.tool_calls), 1)
        self.assertEqual(resp.tool_calls[0].name, "append_row")
        self.assertEqual(resp.usage["prompt_tokens"], 120)
        self.assertEqual(resp.usage["total_tokens"], 150)
        self.assertGreaterEqual(resp.latency_ms, 0.0)


class CostTests(unittest.TestCase):
    def test_known_model_cost(self):
        # gpt-4o: $2.50/1M in, $10.00/1M out.
        cost = observability.compute_cost("gpt-4o", 1_000_000, 1_000_000)
        self.assertAlmostEqual(cost, 12.50, places=4)

    def test_unknown_model_costs_zero(self):
        self.assertEqual(observability.compute_cost("mystery", 1000, 1000), 0.0)


class TrackerSummaryTests(unittest.TestCase):
    def test_record_and_summarize_roundtrip(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "usage.jsonl")
            tracker = observability.UsageTracker(path=path)
            tracker.record_llm_call(
                "gpt-4o",
                {"prompt_tokens": 1000, "completion_tokens": 500, "total_tokens": 1500},
                latency_ms=42.0,
                num_tool_calls=1,
            )
            tracker.record_tool_call("append_row", 3.2, ok=True)
            tracker.record_tool_call("remove_column", 1.0, ok=False, error="boom")

            events = observability.load_events(path)
            summary = observability.summarize(events)
            self.assertEqual(summary["llm_calls"], 1)
            self.assertEqual(summary["tool_calls"], 2)
            self.assertEqual(summary["total_tokens"], 1500)
            self.assertAlmostEqual(summary["total_cost_usd"], 0.0075, places=6)
            self.assertEqual(summary["by_tool"]["remove_column"]["errors"], 1)
            self.assertEqual(summary["by_model"]["gpt-4o"]["calls"], 1)

    def test_in_memory_tracker_writes_no_file(self):
        tracker = observability.UsageTracker(in_memory=True)
        tracker.record_tool_call("append_row", 1.0, ok=True)
        self.assertIsNone(tracker.path)
        self.assertEqual(len(tracker.events), 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
