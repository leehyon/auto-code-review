"""启动时不能被 LLM 连通性检查阻塞端口监听。

背景：GitHub Copilot 的 ping 要先探测 endpoint、再换 token，各带 30 秒超时。
网络不通时启动耗时超过 100 秒，这期间 Web 服务不监听，
webhook 请求会直接连接失败（Bitbucket 的 Test connection 报连接超时）。
"""
import os
import sys
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class StartupDoesNotBlockTest(unittest.TestCase):

    def test_check_config_can_skip_connectivity(self):
        from src.utils.config_checker import check_config
        with patch('src.utils.config_checker.check_llm_connectivity') as ping:
            check_config(check_connectivity=False)
        self.assertFalse(ping.called, 'check_connectivity=False 时不应该做连通性检查')

    def test_check_config_still_checks_env_and_provider(self):
        """跳过连通性检查不能把环境变量和 provider 的检查也一起跳过。"""
        from src.utils.config_checker import check_config
        with patch('src.utils.config_checker.check_env_vars') as env, \
                patch('src.utils.config_checker.check_llm_provider') as prov, \
                patch('src.utils.config_checker.check_llm_connectivity') as ping:
            check_config(check_connectivity=False)
        self.assertTrue(env.called)
        self.assertTrue(prov.called)
        self.assertFalse(ping.called)

    def test_check_config_default_still_checks_connectivity(self):
        """CLI 等离线场景默认还是要做连通性检查。"""
        from src.utils.config_checker import check_config
        with patch('src.utils.config_checker.check_llm_connectivity') as ping:
            check_config()
        self.assertTrue(ping.called)

    def test_api_main_starts_server_without_waiting_for_connectivity(self):
        """start_server 必须在连通性检查还没结束时就已经开始监听端口。"""
        import api

        state = {'server': False, 'connectivity': False, 'connectivity_done': False}

        def slow_check():
            state['connectivity'] = True
            time.sleep(1.5)
            state['connectivity_done'] = True

        def fake_run(*args, **kwargs):
            # 服务开始监听时，连通性检查应该还在跑
            state['server'] = True
            state['connectivity_done_when_server_started'] = state['connectivity_done']

        with patch('api.check_config') as cfg, \
                patch('api.check_llm_connectivity', slow_check), \
                patch('api.setup_scheduler'), \
                patch.object(api.api_app, 'run', fake_run):
            cfg.return_value = None
            api.start_server()

        self.assertTrue(state['server'], '服务没有启动')
        self.assertTrue(state['connectivity'], '连通性检查没有被触发')
        self.assertFalse(state.get('connectivity_done_when_server_started'),
                         '服务在连通性检查完成前就启动了才对')

    def test_start_server_does_not_block_on_connectivity(self):
        """start_server 本身不该等连通性检查结束。"""
        import api

        def slow_check():
            time.sleep(1.5)

        with patch('api.check_config') as cfg, \
                patch('api.check_llm_connectivity', slow_check), \
                patch('api.setup_scheduler'), \
                patch.object(api.api_app, 'run', lambda *a, **kw: None):
            cfg.return_value = None
            t0 = time.time()
            api.start_server()
            elapsed = time.time() - t0

        self.assertLess(elapsed, 1.0,
                        f'start_server 花了 {elapsed:.1f}s，说明被连通性检查阻塞了')


if __name__ == "__main__":
    unittest.main()
