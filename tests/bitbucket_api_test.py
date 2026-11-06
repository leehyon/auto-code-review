"""
Bitbucket webhook 路由（api.py）的集成测试。

使用 Flask test client 验证：
- X-Event-Key header 的平台识别与分发
- 凭证 / URL 的解析与错误提示
- 与 GitHub / Gitea / GitLab 的路由互不干扰

运行方式：``python3 -m unittest discover -s tests -p "bitbucket_api_test.py" -v``
"""

import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

PR_PAYLOAD = {
    "eventKey": "pr:opened",
    "actor": {"name": "admin"},
    "pullRequest": {
        "id": 11,
        "state": "OPEN",
        "fromRef": {
            "displayId": "feature",
            "repository": {
                "slug": "demo", "name": "demo",
                "project": {"key": "PROJ"},
                "links": {"self": [{"href": "http://bb.example.com/bitbucket/projects/PROJ/repos/demo/browse"}]},
            },
        },
        "toRef": {
            "displayId": "master",
            "repository": {
                "slug": "demo", "name": "demo",
                "project": {"key": "PROJ"},
                "links": {"self": [{"href": "http://bb.example.com/bitbucket/projects/PROJ/repos/demo/browse"}]},
            },
        },
        "author": {"user": {"name": "admin"}},
    },
}

PUSH_PAYLOAD = {
    "eventKey": "repo:refs_changed",
    "actor": {"name": "admin"},
    "repository": {
        "slug": "demo", "name": "demo",
        "project": {"key": "PROJ"},
        "links": {"self": [{"href": "http://bb.example.com/bitbucket/projects/PROJ/repos/demo/browse"}]},
    },
    "changes": [],
    "commits": [],
}


class TestBitbucketApiRouting(unittest.TestCase):

    def setUp(self):
        import api
        self.api = api
        self.client = api.api_app.test_client()
        os.environ['BITBUCKET_ACCESS_TOKEN'] = 'test-token'
        os.environ['BITBUCKET_USERNAME'] = 'admin'
        os.environ.pop('BITBUCKET_URL', None)
        self.addCleanup(os.environ.pop, 'BITBUCKET_ACCESS_TOKEN', None)
        self.addCleanup(os.environ.pop, 'BITBUCKET_USERNAME', None)
        self.addCleanup(os.environ.pop, 'BITBUCKET_URL', None)

    def _post(self, payload, headers):
        with mock.patch.object(self.api, 'handle_queue_with_username') as handle_queue:
            response = self.client.post('/review/webhook', json=payload, headers=headers)
        return response, handle_queue

    def test_pr_event_routed_to_bitbucket_worker(self):
        response, handle_queue = self._post(PR_PAYLOAD, {'X-Event-Key': 'pr:opened'})
        self.assertEqual(response.status_code, 200)
        self.assertIn('Bitbucket', response.get_json()['message'])
        handle_queue.assert_called_once()
        args = handle_queue.call_args[0]
        self.assertIs(args[0], self.api.handle_bitbucket_pull_request_event)
        # URL 从 payload 中推导得出
        self.assertEqual(args[3], 'http://bb.example.com/bitbucket')
        # 用户名随任务一起传递给 worker（HTTP Basic 认证需要）
        self.assertEqual(args[5], 'admin')

    def test_push_event_routed_to_bitbucket_worker(self):
        response, handle_queue = self._post(PUSH_PAYLOAD, {'X-Event-Key': 'repo:refs_changed'})
        self.assertEqual(response.status_code, 200)
        args = handle_queue.call_args[0]
        self.assertIs(args[0], self.api.handle_bitbucket_push_event)

    def test_source_branch_update_triggers_review(self):
        payload = dict(PR_PAYLOAD)
        payload['eventKey'] = 'pr:from_ref_updated'
        response, handle_queue = self._post(payload, {'X-Event-Key': 'pr:from_ref_updated'})
        self.assertEqual(response.status_code, 200)
        handle_queue.assert_called_once()
        self.assertIs(handle_queue.call_args[0][0], self.api.handle_bitbucket_pull_request_event)

    def test_other_pr_events_ignored(self):
        response, handle_queue = self._post(PR_PAYLOAD, {'X-Event-Key': 'pr:merged'})
        self.assertEqual(response.status_code, 200)
        handle_queue.assert_not_called()
        self.assertIn('ignored', response.get_json()['message'])

    def test_non_review_repo_events_ignored(self):
        response, handle_queue = self._post(PUSH_PAYLOAD, {'X-Event-Key': 'repo:modified'})
        self.assertEqual(response.status_code, 200)
        handle_queue.assert_not_called()

    def test_missing_token_returns_400(self):
        os.environ.pop('BITBUCKET_ACCESS_TOKEN', None)
        response, handle_queue = self._post(PR_PAYLOAD, {'X-Event-Key': 'pr:opened'})
        self.assertEqual(response.status_code, 400)
        self.assertIn('access token', response.get_json()['message'])
        handle_queue.assert_not_called()

    def test_bitbucket_url_env_takes_precedence(self):
        os.environ['BITBUCKET_URL'] = 'http://configured.example.com'
        response, handle_queue = self._post(PR_PAYLOAD, {'X-Event-Key': 'pr:opened'})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(handle_queue.call_args[0][3], 'http://configured.example.com')

    def test_basic_auth_header_used_when_env_missing(self):
        import base64
        os.environ.pop('BITBUCKET_ACCESS_TOKEN', None)
        os.environ.pop('BITBUCKET_USERNAME', None)
        credentials = base64.b64encode(b'webhookuser:webhooktoken').decode()
        response, handle_queue = self._post(PR_PAYLOAD, {
            'X-Event-Key': 'pr:opened',
            'Authorization': f'Basic {credentials}',
        })
        self.assertEqual(response.status_code, 200)
        args = handle_queue.call_args[0]
        self.assertEqual(args[2], 'webhooktoken')
        self.assertEqual(args[3], 'http://bb.example.com/bitbucket')
        # Basic 认证头中的用户名也要传给 worker
        self.assertEqual(args[5], 'webhookuser')

    def test_github_event_not_routed_to_bitbucket(self):
        os.environ['GITHUB_ACCESS_TOKEN'] = 'github-token'
        self.addCleanup(os.environ.pop, 'GITHUB_ACCESS_TOKEN', None)
        github_payload = {"action": "opened", "repository": {"name": "demo", "full_name": "o/demo"},
                          "pull_request": {"number": 1, "user": {"login": "u"},
                                           "head": {"ref": "f"}, "base": {"ref": "m"},
                                           "html_url": "http://x"}}
        with mock.patch.object(self.api, 'handle_queue') as handle_queue:
            response = self.client.post('/review/webhook', json=github_payload,
                                        headers={'X-GitHub-Event': 'pull_request'})
        self.assertEqual(response.status_code, 200)
        self.assertIs(handle_queue.call_args[0][0], self.api.handle_github_pull_request_event)

    def test_gitea_event_not_routed_to_bitbucket(self):
        os.environ['GITEA_ACCESS_TOKEN'] = 'gitea-token'
        os.environ['GITEA_URL'] = 'http://gitea.example.com'
        self.addCleanup(os.environ.pop, 'GITEA_ACCESS_TOKEN', None)
        self.addCleanup(os.environ.pop, 'GITEA_URL', None)
        gitea_payload = {"action": "opened", "repository": {"name": "demo", "full_name": "o/demo"},
                         "pull_request": {"number": 1, "user": {"login": "u"},
                                          "head": {"ref": "f"}, "base": {"ref": "m"},
                                          "html_url": "http://x"}}
        with mock.patch.object(self.api, 'handle_queue') as handle_queue:
            response = self.client.post('/review/webhook', json=gitea_payload,
                                        headers={'X-Gitea-Event': 'pull_request',
                                                 'X-GitHub-Event': 'pull_request'})
        self.assertEqual(response.status_code, 200)
        self.assertIs(handle_queue.call_args[0][0], self.api.handle_gitea_pull_request_event)

    def test_bitbucket_cloud_event_not_misrouted(self):
        """Bitbucket Cloud 使用 pullrequest: / repo:push，不应被识别为 DC 事件。"""
        cloud_payload = {"repository": {"name": "demo"}}
        with mock.patch.object(self.api, 'handle_queue') as handle_queue:
            response = self.client.post('/review/webhook', json=cloud_payload,
                                        headers={'X-Event-Key': 'pullrequest:created'})
        # 未被识别为 Bitbucket，会走 GitLab 分支（缺少 token 返回 400）
        self.assertEqual(response.status_code, 400)
        handle_queue.assert_not_called()


class TestBitbucketEventDetection(unittest.TestCase):

    def test_dc_events_detected(self):
        import api
        for event in ['pr:opened', 'pr:from_ref_updated', 'pr:merged', 'repo:refs_changed',
                      'repo:modified', 'repo:comment:added']:
            self.assertTrue(api.is_bitbucket_event(event), event)

    def test_cloud_events_not_detected(self):
        import api
        for event in ['pullrequest:created', 'repo:push', 'pullrequest:updated']:
            self.assertFalse(api.is_bitbucket_event(event), event)

    def test_empty_event(self):
        import api
        self.assertFalse(api.is_bitbucket_event(''))


class TestExtractBitbucketUrl(unittest.TestCase):

    def test_extract_from_repository_links(self):
        import api
        url = api.extract_bitbucket_url(PUSH_PAYLOAD)
        self.assertEqual(url, 'http://bb.example.com/bitbucket')

    def test_extract_from_pull_request_to_ref(self):
        import api
        url = api.extract_bitbucket_url(PR_PAYLOAD)
        self.assertEqual(url, 'http://bb.example.com/bitbucket')

    def test_returns_empty_when_no_link(self):
        import api
        self.assertEqual(api.extract_bitbucket_url({'repository': {}}), '')
        self.assertEqual(api.extract_bitbucket_url({}), '')

    def test_extract_from_personal_project_links(self):
        """个人项目（personal project）的仓库链接挂在 /users/<username>/ 下。"""
        import api
        payload = {
            'pullRequest': {
                'toRef': {
                    'repository': {
                        'links': {'self': [
                            {'href': 'https://bb.example.com/users/liyahong/repos/emirror-eol/browse'}]},
                    },
                },
            },
        }
        self.assertEqual(api.extract_bitbucket_url(payload), 'https://bb.example.com')

    def test_extract_from_personal_project_repository_links(self):
        import api
        payload = {
            'repository': {
                'links': {'self': [
                    {'href': 'https://bb.example.com/users/liyahong/repos/emirror-eol/browse'}]},
            },
        }
        self.assertEqual(api.extract_bitbucket_url(payload), 'https://bb.example.com')


if __name__ == '__main__':
    unittest.main(verbosity=2)
