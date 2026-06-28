"""Tests for the outbound-call retry wrapper."""

import unittest

from sheets_agent.retry import is_retryable, with_retry


class FourxxError(Exception):
    """Mimics a client library 4xx error exposing a status_code."""

    def __init__(self, code=400):
        super().__init__(f"HTTP {code}")
        self.status_code = code


class RetryTests(unittest.TestCase):
    def test_succeeds_after_transient_connection_error(self):
        calls = {"n": 0}

        def flaky():
            calls["n"] += 1
            if calls["n"] < 3:
                raise ConnectionResetError(54, "Connection reset by peer")
            return "ok"

        # base_delay=0 keeps the test fast.
        result = with_retry(flaky, attempts=3, base_delay=0, label="test")
        self.assertEqual(result, "ok")
        self.assertEqual(calls["n"], 3)  # retried twice, then succeeded

    def test_broken_pipe_is_retried(self):
        calls = {"n": 0}

        def flaky():
            calls["n"] += 1
            if calls["n"] == 1:
                raise BrokenPipeError(32, "Broken pipe")
            return "recovered"

        self.assertEqual(with_retry(flaky, base_delay=0), "recovered")
        self.assertEqual(calls["n"], 2)

    def test_does_not_retry_on_4xx(self):
        calls = {"n": 0}

        def client_error():
            calls["n"] += 1
            raise FourxxError(400)

        self.assertFalse(is_retryable(FourxxError(404)))
        with self.assertRaises(FourxxError):
            with_retry(client_error, attempts=3, base_delay=0)
        self.assertEqual(calls["n"], 1)  # tried once, no retries

    def test_does_not_retry_on_value_error(self):
        calls = {"n": 0}

        def logic_error():
            calls["n"] += 1
            raise ValueError("unknown column")

        with self.assertRaises(ValueError):
            with_retry(logic_error, attempts=3, base_delay=0)
        self.assertEqual(calls["n"], 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
