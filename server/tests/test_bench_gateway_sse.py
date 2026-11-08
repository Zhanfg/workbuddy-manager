"""Secret-free SSE throughput meter regression tests."""
from __future__ import annotations

import json
import unittest
from argparse import Namespace
from pathlib import Path
from tempfile import TemporaryDirectory

from tools.bench_gateway_sse import extract_metrics, prepare_payload


class GatewaySSEBenchmarkTests(unittest.TestCase):
    def test_chat_usage_and_text(self):
        frame = {
            "choices": [{"delta": {"content": "x"}}],
            "usage": {"prompt_tokens": 24, "completion_tokens": 48},
        }
        self.assertEqual(extract_metrics(frame, ""), (True, 24, 48))

    def test_responses_delta_and_terminal_usage(self):
        frame = {"type": "response.output_text.delta", "delta": "x"}
        self.assertEqual(extract_metrics(frame, "response.output_text.delta"), (True, None, None))
        end = {"type": "response.completed", "response": {
            "usage": {"input_tokens": 12, "output_tokens": 34},
        }}
        self.assertEqual(extract_metrics(end, "response.completed"), (False, 12, 34))

    def test_payload_reuses_existing_file_and_forces_stream(self):
        with TemporaryDirectory() as path:
            input_path = Path(path) / "payload.json"
            input_path.write_text(json.dumps({"model": "m", "stream": False}))
            args = Namespace(
                payload=str(input_path), prompt_kib=1,
                model="m", protocol="chat", max_output_tokens=128,
            )
            self.assertEqual(prepare_payload(args), {"model": "m", "stream": True})


if __name__ == "__main__":
    unittest.main()
