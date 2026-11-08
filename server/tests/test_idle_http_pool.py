"""连接池空闲回收不应中断 SSE、代理会话或共享客户端。"""
from __future__ import annotations

import asyncio
import unittest

from server import config


class IdleHttpPoolTests(unittest.TestCase):
    def tearDown(self):
        asyncio.run(config.close_clients())

    def test_borrowed_client_survives_reaper(self):
        async def check():
            async with config.http_client(12, connect=4) as cli:
                self.assertEqual(await config.reap_idle_http_clients(0), 0)
                self.assertFalse(cli.is_closed)
            self.assertEqual(await config.reap_idle_http_clients(0), 1)
            self.assertTrue(cli.is_closed)
        asyncio.run(check())

    def test_two_sse_leases_recycle_only_after_final_release(self):
        async def check():
            a = config.http_client(13, connect=4)
            b = config.http_client(13, connect=4)
            self.assertIs(a._client, b._client)
            await a.aclose()
            self.assertEqual(await config.reap_idle_http_clients(0), 0)
            self.assertFalse(b.is_closed)
            await b.aclose()
            self.assertEqual(await config.reap_idle_http_clients(0), 1)
        asyncio.run(check())

    def test_reborrow_still_reuses_live_connection(self):
        async def check():
            async with config.http_client(14, connect=4) as cli:
                first = cli
            async with config.http_client(14, connect=4) as cli:
                self.assertIs(first, cli)
            self.assertEqual(await config.reap_idle_http_clients(0), 1)
            async with config.http_client(14, connect=4) as cli:
                self.assertIsNot(cli, first)
        asyncio.run(check())

    def test_active_lease_not_closed_by_capacity_eviction(self):
        async def check():
            original = config._CLIENTS_MAX
            config._CLIENTS_MAX = 1
            try:
                one = config.http_client(20, connect=4)
                two = config.http_client(21, connect=4)
                self.assertFalse(one.is_closed)
                self.assertFalse(two.is_closed)
                await one.aclose()
                config._evict(asyncio.get_running_loop())
                self.assertFalse(two.is_closed)
                await two.aclose()
            finally:
                config._CLIENTS_MAX = original
        asyncio.run(check())


if __name__ == '__main__':
    unittest.main()
