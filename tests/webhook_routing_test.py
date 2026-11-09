"""webhook 路由：平台识别、eventKey 兜底、无平台头时的明确报错。

背景：Bitbucket 的 X-Event-Key header 在某些链路（反向代理、企业网关）会被丢掉，
请求因此落进 GitLab 兜底分支，报一个和真实原因无关的 "Missing GitLab URL"。
"""
import os
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from api import api_app


class WebhookRoutingTest(unittest.TestCase):

    def setUp(self):
        self.client = api_app.test_client()
        # 隔离本地环境里的真实配置
        for var in ('GITLAB_URL', 'GITLAB_ACCESS_TOKEN', 'BITBUCKET_URL',
                    'BITBUCKET_ACCESS_TOKEN', 'BITBUCKET_WEBHOOK_SECRET'):
            os.environ.pop(var, None)

    def _post(self, payload, headers=None):
        return self.client.post('/review/webhook', json=payload, headers=headers or {})

    def test_bitbucket_header_routes_to_bitbucket(self):
        """带 X-Event-Key 时必须走 Bitbucket 分支，不能落进 GitLab。"""
        payload = {'eventKey': 'pr:opened', 'pullRequest': {'id': 1}}
        with patch('api.handle_bitbucket_webhook') as handler:
            handler.return_value = ({'message': 'ok'}, 200)
            resp = self._post(payload, {'X-Event-Key': 'pr:opened'})
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(handler.called)
        args = handler.call_args[0]
        self.assertEqual(args[0], 'pr:opened')

    def test_payload_eventkey_is_fallback_when_header_missing(self):
        """header 丢了时，用 payload 里的 eventKey 兜底识别 Bitbucket。"""
        payload = {'eventKey': 'pr:opened', 'pullRequest': {'id': 1}}
        with patch('api.handle_bitbucket_webhook') as handler:
            handler.return_value = ({'message': 'ok'}, 200)
            resp = self._post(payload)          # 不带任何 header
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(handler.called, 'payload.eventKey 没有被用作兜底识别')
        self.assertEqual(handler.call_args[0][0], 'pr:opened')

    def test_payload_eventkey_repo_event(self):
        payload = {'eventKey': 'repo:refs_changed', 'repository': {'slug': 'x'}}
        with patch('api.handle_bitbucket_webhook') as handler:
            handler.return_value = ({'message': 'ok'}, 200)
            resp = self._post(payload)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(handler.call_args[0][0], 'repo:refs_changed')

    def test_no_event_header_and_no_eventkey_says_unrecognized(self):
        """没有任何平台标识时，要说「认不出来」，不能报 Missing GitLab URL。"""
        resp = self._post({'some': 'payload'})
        self.assertEqual(resp.status_code, 400)
        body = resp.get_json()
        self.assertIn('Unrecognized webhook', body['message'])
        self.assertNotIn('GitLab', body['message'])

    def test_gitea_header_takes_priority(self):
        """Gitea 同时发 X-GitHub-Event 和 X-Gitea-Event，Gitea 优先。"""
        payload = {'eventKey': 'pr:opened'}
        headers = {'X-Gitea-Event': 'push', 'X-GitHub-Event': 'push',
                   'X-Event-Key': 'pr:opened'}
        with patch('api.handle_gitea_webhook') as gitea, \
                patch('api.handle_bitbucket_webhook') as bb:
            gitea.return_value = ({'message': 'ok'}, 200)
            resp = self._post(payload, headers)
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(gitea.called)
        self.assertFalse(bb.called)

    def test_github_header_routes_to_github(self):
        with patch('api.handle_github_webhook') as handler:
            handler.return_value = ({'message': 'ok'}, 200)
            resp = self._post({'some': 'payload'}, {'X-GitHub-Event': 'pull_request'})
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(handler.called)

    def test_cloud_event_key_is_not_treated_as_bitbucket(self):
        """Bitbucket Cloud 的 pullrequest: 不能被误判成 DC。"""
        payload = {'eventKey': 'pullrequest:created'}
        resp = self._post(payload)
        # 不识别成 Bitbucket，且不是"完全无法识别"（有 eventKey）
        body = resp.get_json()
        self.assertNotIn('Unrecognized webhook', body.get('message', ''))


class BitbucketPingTest(unittest.TestCase):
    """diagnostics:ping 是 Bitbucket 配置页「Test connection」按钮发的事件。"""

    def setUp(self):
        self.client = api_app.test_client()

    def test_ping_event_is_recognized(self):
        from api import is_bitbucket_event
        self.assertTrue(is_bitbucket_event('diagnostics:ping'))

    def test_ping_returns_pong(self):
        """ping 必须回 200，否则配置页的测试按钮一直显示失败。"""
        resp = self.client.post('/review/webhook', json={'eventKey': 'diagnostics:ping'},
                                headers={'X-Event-Key': 'diagnostics:ping'})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.get_json().get('message'), 'pong')

    def test_ping_does_not_require_signature(self):
        """探测事件没有副作用，不该因为签名缺失而报错。"""
        os.environ['BITBUCKET_WEBHOOK_SECRET'] = 'some-secret'
        self.addCleanup(os.environ.pop, 'BITBUCKET_WEBHOOK_SECRET', None)
        resp = self.client.post('/review/webhook', json={'eventKey': 'diagnostics:ping'},
                                headers={'X-Event-Key': 'diagnostics:ping'})
        self.assertEqual(resp.status_code, 200)

    def test_ping_does_not_trigger_review(self):
        """ping 不能触发审查。"""
        with patch('api.handle_queue') as q:
            resp = self.client.post('/review/webhook', json={'eventKey': 'diagnostics:ping'},
                                    headers={'X-Event-Key': 'diagnostics:ping'})
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(q.called)

    def test_other_events_still_require_signature(self):
        """不能因为放行 ping 而放宽其它事件的签名校验。"""
        os.environ['BITBUCKET_WEBHOOK_SECRET'] = 'some-secret'
        self.addCleanup(os.environ.pop, 'BITBUCKET_WEBHOOK_SECRET', None)
        resp = self.client.post('/review/webhook', json={'eventKey': 'repo:modified'},
                                headers={'X-Event-Key': 'repo:modified'})
        self.assertEqual(resp.status_code, 403)


if __name__ == "__main__":
    unittest.main()
