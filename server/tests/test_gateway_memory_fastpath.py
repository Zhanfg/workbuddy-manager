"""网关大请求与高频 SSE 的低分配路径回归测试。

不依赖真实网关 Key、账号池或外部网络。
"""
from __future__ import annotations

import asyncio
import json
import unittest
from unittest import mock

from starlette.requests import Request

from server.routers import gateway


def chunked_request(parts: list[bytes]) -> Request:
    scope = {
        'type': 'http',
        'method': 'POST',
        'path': '/v1/chat/completions',
        'headers': [],
    }
    # 最后一片使用 more_body=False，模拟合法 chunked 请求。
    position = 0

    async def receive_chunk():
        nonlocal position
        if position >= len(parts):
            return {'type': 'http.disconnect'}
        chunk = parts[position]
        position += 1
        return {'type': 'http.request', 'body': chunk,
                'more_body': position < len(parts)}

    return Request(scope, receive_chunk)


class GatewayMemoryFastpathTest(unittest.TestCase):
    def test_chunked_json_parses_without_content_length(self):
        obj = {'model': 'deepseek-v4.1-flash',
               'messages': [{'role': 'user', 'content': '数据' * 1000}]}
        raw = json.dumps(obj, ensure_ascii=False).encode('utf-8')
        parts = [raw[i:i+37] for i in range(0, len(raw), 37)]
        parsed, err = asyncio.run(gateway._read_json_body(chunked_request(parts)))
        self.assertIsNone(err)
        self.assertEqual(parsed, obj)

    def test_stream_after_first_text_only_parses_usage_frames(self):
        usage = {}
        event = 'data: {"choices":[{"delta":{"content":"A"}}]}\\n'
        pending, found = gateway._scan_sse(event, usage)
        self.assertTrue(found)
        self.assertEqual(pending, '')

        delta = 'data: {"choices":[{"delta":{"content":"B"}}]}\\n'
        final = 'data: {"choices":[],"usage":{"prompt_tokens":12,"completion_tokens":34}}\\n'
        decoder = json.loads
        with mock.patch.object(gateway.json, 'loads', wraps=decoder) as patched:
            rest, found = gateway._scan_sse(
                delta * 100 + final, usage, check_content=False,
            )
        self.assertEqual(rest, '')
        self.assertFalse(found)
        self.assertEqual(usage['prompt_tokens'], 12)
        self.assertEqual(usage['completion_tokens'], 34)
        self.assertEqual(patched.call_count, 1)

    def test_first_text_detection_unchanged(self):
        pending, found = gateway._scan_sse(
            'data: {"choices":[{"delta":{"role":"assistant"}}]}\\n'
            'data: {"choices":[{"delta":{"content":"hello"}}]}\\n', {},
        )
        self.assertEqual(pending, '')
        self.assertTrue(found)


if __name__ == '__main__':
    unittest.main()
