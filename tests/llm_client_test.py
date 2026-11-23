"""
GitHub Copilot LLM 客户端的单元测试。

覆盖：
- Factory 注册
- 默认值与环境变量覆盖
- token 判定（GitHub PAT vs 已换好的 Copilot token）、交换与缓存
- Responses API（默认）：URL、请求头、body、返回解析
- Chat Completions API（可选模式）：401 刷新重试、404 模型不存在
- 模型列表

运行方式：``python3 -m unittest discover -s tests -p "*_test.py" -v``
"""

import json
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.llm.client.github_copilot import GitHubCopilotClient
from src.llm.factory import Factory


class FakeResponse:
    def __init__(self, status_code=200, json_data=None, text=''):
        self.status_code = status_code
        self._json_data = json_data
        self.text = text or (json.dumps(json_data) if json_data is not None else '')

    def json(self):
        return self._json_data


RESPONSES_BODY = {
    "id": "resp_1", "object": "response", "status": "completed", "model": "gpt-6-luna",
    "output": [{"type": "message", "role": "assistant",
                "content": [{"type": "output_text", "text": "responses output"}]}],
    "output_text": "responses output",
}


def fake_completion(content='reviewed'):
    class Message:
        role = 'assistant'

    class Choice:
        def __init__(self):
            self.message = Message()
            self.message.content = content

    class Completion:
        choices = [Choice()]

    return Completion()


class TestFactory(unittest.TestCase):

    def test_github_copilot_registered(self):
        client = Factory().getClient('github_copilot')
        self.assertIsInstance(client, GitHubCopilotClient)

    def test_unknown_provider_raises(self):
        with self.assertRaises(Exception):
            Factory().getClient('not_a_provider')


class TestGitHubCopilotClient(unittest.TestCase):

    def setUp(self):
        # 清掉环境里的真实凭据：这些测试绝不能发真实请求（会消耗额度）
        for key in ('GITHUB_COPILOT_TOKEN', 'GITHUB_TOKEN', 'GITHUB_COPILOT_MODEL',
                    'GITHUB_COPILOT_API_BASE_URL', 'GITHUB_COPILOT_API_MODE'):
            os.environ.pop(key, None)
        # 换 token 的结论是进程级的，每个用例都要从干净状态开始
        import src.llm.client.github_copilot as mod
        mod.reset_exchange_cache()

    def tearDown(self):
        import src.llm.client.github_copilot as mod
        mod.reset_exchange_cache()

    def _client(self, **env):
        for k, v in env.items():
            os.environ[k] = v
        return GitHubCopilotClient()

    def test_default_base_url(self):
        self.assertEqual(self._client().base_url, 'https://api.githubcopilot.com')

    def test_default_model(self):
        self.assertEqual(self._client().default_model, 'gpt-4o')

    def test_default_api_mode_is_responses(self):
        self.assertEqual(self._client().api_mode, 'responses')

    def test_model_from_env(self):
        self.assertEqual(self._client(GITHUB_COPILOT_MODEL='gpt-6-luna').default_model,
                         'gpt-6-luna')

    def test_base_url_from_env(self):
        client = self._client(GITHUB_COPILOT_API_BASE_URL='https://api.hub.example.com/v1/')
        self.assertEqual(client.base_url, 'https://api.hub.example.com/v1')

    def test_api_mode_from_env(self):
        client = self._client(GITHUB_COPILOT_API_MODE='chat_completions')
        self.assertEqual(client.api_mode, 'chat_completions')

    def test_unknown_api_mode_falls_back(self):
        client = self._client(GITHUB_COPILOT_API_MODE='nonsense')
        self.assertEqual(client.api_mode, 'responses')

    def test_github_token_prefixes_detected(self):
        for token in ('ghp_xxx', 'gho_xxx', 'ghu_xxx', 'ghs_xxx', 'ghr_xxx', 'github_pat_xxx'):
            self.assertTrue(GitHubCopilotClient._is_github_token(token), token)

    def test_copilot_token_not_detected_as_github_token(self):
        self.assertFalse(GitHubCopilotClient._is_github_token('eyJhbGciOiJSUzI1NiIs'))
        self.assertFalse(GitHubCopilotClient._is_github_token(''))
        self.assertFalse(GitHubCopilotClient._is_github_token(None))

    def test_github_token_is_exchanged_for_copilot_token(self):
        client = self._client(GITHUB_COPILOT_TOKEN='ghp_pat')
        with mock.patch.object(GitHubCopilotClient, '_discover_endpoint', return_value=None), \
                mock.patch('src.llm.client.github_copilot.requests.get',
                           return_value=FakeResponse(200, {'token': 'copilot-token',
                                                            'expires_at': 9999999999})) as get:
            token = client._resolve_token()
        self.assertEqual(token, 'copilot-token')
        self.assertEqual(get.call_count, 1)
        self.assertEqual(get.call_args[1]['headers']['Authorization'], 'Bearer ghp_pat')

    def test_copilot_token_is_used_directly(self):
        client = self._client(GITHUB_COPILOT_TOKEN='eyJhbGciOi')
        with mock.patch('src.llm.client.github_copilot.requests.get') as get:
            token = client._resolve_token()
        self.assertEqual(token, 'eyJhbGciOi')
        self.assertEqual(get.call_count, 0)

    def test_falls_back_to_github_token(self):
        client = self._client(GITHUB_TOKEN='ghp_fallback')
        with mock.patch('src.llm.client.github_copilot.requests.get',
                        return_value=FakeResponse(200, {'token': 'exchanged'})):
            self.assertEqual(client._resolve_token(), 'exchanged')

    def test_no_token_at_all(self):
        self.assertIsNone(self._client()._resolve_token())

    def test_exchange_failure_falls_back_to_raw_token(self):
        """实测部分订阅类型（business）换 token 会 403，这时原始 token 直接当 Bearer 可用。"""
        import src.llm.client.github_copilot as mod
        mod.reset_exchange_cache()
        client = self._client(GITHUB_COPILOT_TOKEN='ghp_pat')
        with mock.patch.object(GitHubCopilotClient, '_discover_endpoint', return_value=None), \
                mock.patch('src.llm.client.github_copilot.requests.get',
                           return_value=FakeResponse(403, None, 'Forbidden')):
            token = client._resolve_token()
        self.assertEqual(token, 'ghp_pat')
        # 记住换 token 不可用，后面不再重复请求（进程级结论）
        self.assertTrue(mod._EXCHANGE_UNAVAILABLE)

    def test_exchange_not_retried_after_failure(self):
        import src.llm.client.github_copilot as mod
        mod.reset_exchange_cache()
        client = self._client(GITHUB_COPILOT_TOKEN='ghp_pat')
        with mock.patch.object(GitHubCopilotClient, '_discover_endpoint', return_value=None), \
                mock.patch('src.llm.client.github_copilot.requests.get',
                           return_value=FakeResponse(403, None, 'Forbidden')) as get:
            client._resolve_token()
            client._resolve_token()
        # 第二次直接用原始 token，不再请求
        self.assertEqual(get.call_count, 1)

    def test_endpoint_is_discovered_for_github_token(self):
        """不同订阅类型域名不同，要向 GitHub 查 endpoints.api。"""
        client = self._client(GITHUB_COPILOT_TOKEN='ghp_pat')
        with mock.patch.object(GitHubCopilotClient, '_discover_endpoint',
                               return_value='https://api.business.githubcopilot.com') as discover, \
                mock.patch('src.llm.client.github_copilot.requests.get',
                           return_value=FakeResponse(200, {'token': 'copilot-token',
                                                            'expires_at': 9999999999})):
            client._resolve_token()
        self.assertEqual(discover.call_count, 1)
        self.assertEqual(client.base_url, 'https://api.business.githubcopilot.com')

    def test_explicit_base_url_skips_discovery(self):
        client = self._client(GITHUB_COPILOT_TOKEN='ghp_pat',
                              GITHUB_COPILOT_API_BASE_URL='https://api.hub.example.com/v1')
        with mock.patch.object(GitHubCopilotClient, '_discover_endpoint') as discover, \
                mock.patch('src.llm.client.github_copilot.requests.get',
                           return_value=FakeResponse(200, {'token': 'copilot-token',
                                                            'expires_at': 9999999999})):
            client._resolve_token()
        self.assertEqual(discover.call_count, 0)
        self.assertEqual(client.base_url, 'https://api.hub.example.com/v1')

    def test_exchanged_token_is_cached(self):
        client = self._client(GITHUB_COPILOT_TOKEN='ghp_pat')
        with mock.patch.object(GitHubCopilotClient, '_discover_endpoint', return_value=None), \
                mock.patch('src.llm.client.github_copilot.requests.get',
                           return_value=FakeResponse(200, {'token': 'copilot-token',
                                                            'expires_at': 9999999999})) as get:
            client._resolve_token()
            client._resolve_token()
        # 第二次直接用缓存，不再请求
        self.assertEqual(get.call_count, 1)

    def test_responses_request_shape(self):
        client = self._client(GITHUB_COPILOT_TOKEN='eyJhbGciOi',
                              GITHUB_COPILOT_API_BASE_URL='https://api.hub.example.com/v1',
                              GITHUB_COPILOT_MODEL='gpt-6-luna')
        messages = [{'role': 'system', 'content': 'sys'},
                    {'role': 'user', 'content': 'user'}]
        with mock.patch('src.llm.client.github_copilot.requests.post',
                        return_value=FakeResponse(200, RESPONSES_BODY)) as post:
            result = client.completions(messages=messages)

        self.assertEqual(result, 'responses output')
        self.assertEqual(post.call_count, 1)
        kwargs = post.call_args[1]
        # url 是位置参数
        self.assertEqual(post.call_args[0][0], 'https://api.hub.example.com/v1/responses')
        self.assertEqual(kwargs['json'], {'model': 'gpt-6-luna', 'input': messages})
        headers = kwargs['headers']
        self.assertEqual(headers['Authorization'], 'Bearer eyJhbGciOi')
        self.assertEqual(headers['OpenAI-Intent'], 'responses')
        self.assertEqual(headers['Editor-Version'], 'vscode/1.95.0')
        self.assertEqual(headers['Copilot-Integration-Id'], 'vscode-chat')
        self.assertEqual(headers['User-Agent'], 'GitHubCopilotChat/0.26.7')

    def test_responses_output_text_fallback(self):
        body = {k: v for k, v in RESPONSES_BODY.items() if k != 'output_text'}
        self.assertEqual(GitHubCopilotClient._extract_output_text(body), 'responses output')
        self.assertEqual(GitHubCopilotClient._extract_output_text(RESPONSES_BODY),
                         'responses output')

    def test_responses_error_status_raises(self):
        client = self._client(GITHUB_COPILOT_TOKEN='eyJhbGciOi')
        with mock.patch('src.llm.client.github_copilot.requests.post',
                        return_value=FakeResponse(403, None, 'token not authorized')):
            with self.assertRaises(ValueError) as ctx:
                client.completions(messages=[{'role': 'user', 'content': 'hi'}])
        self.assertIn('403', str(ctx.exception))

    def test_responses_empty_output_raises(self):
        client = self._client(GITHUB_COPILOT_TOKEN='eyJhbGciOi')
        with mock.patch('src.llm.client.github_copilot.requests.post',
                        return_value=FakeResponse(200, {'output': []})):
            with self.assertRaises(ValueError):
                client.completions(messages=[{'role': 'user', 'content': 'hi'}])

    def test_no_token_raises_before_calling(self):
        with self.assertRaises(ValueError):
            self._client().completions(messages=[{'role': 'user', 'content': 'hi'}])

    def test_chat_completions_mode_uses_sdk(self):
        client = self._client(GITHUB_COPILOT_TOKEN='eyJhbGciOi',
                              GITHUB_COPILOT_API_MODE='chat_completions',
                              GITHUB_COPILOT_MODEL='gpt-6-luna')
        create = mock.MagicMock(return_value=fake_completion('chat output'))
        openai_client = mock.MagicMock()
        openai_client.chat.completions.create = create
        with mock.patch.object(GitHubCopilotClient, '_get_client', return_value=openai_client):
            result = client.completions(messages=[{'role': 'user', 'content': 'hi'}])
        self.assertEqual(result, 'chat output')
        kwargs = create.call_args[1]
        self.assertEqual(kwargs['model'], 'gpt-6-luna')
        self.assertEqual(kwargs['messages'], [{'role': 'user', 'content': 'hi'}])

    def test_chat_completions_headers_include_openai_intent(self):
        client = self._client(GITHUB_COPILOT_TOKEN='eyJhbGciOi',
                              GITHUB_COPILOT_API_MODE='chat_completions')
        token = client._resolve_token()
        headers = client._get_client(token).default_headers
        self.assertEqual(headers['OpenAI-Intent'], 'conversation-panel')
        self.assertEqual(headers['Editor-Version'], 'vscode/1.95.0')
        self.assertEqual(headers['Copilot-Integration-Id'], 'vscode-chat')

    def test_401_triggers_token_refresh_and_retry(self):
        client = self._client(GITHUB_COPILOT_TOKEN='ghp_pat',
                              GITHUB_COPILOT_API_MODE='chat_completions')

        class Unauthorized(Exception):
            def __init__(self):
                self.response = mock.MagicMock()
                self.response.status_code = 401

        create = mock.MagicMock(side_effect=[Unauthorized(), fake_completion('after-retry')])
        openai_client = mock.MagicMock()
        openai_client.chat.completions.create = create

        with mock.patch.object(GitHubCopilotClient, '_get_client', return_value=openai_client), \
                mock.patch('src.llm.client.github_copilot.requests.get',
                           return_value=FakeResponse(200, {'token': 'fresh-token',
                                                            'expires_at': 9999999999})):
            result = client.completions(messages=[{'role': 'user', 'content': 'hi'}])

        self.assertEqual(result, 'after-retry')
        self.assertEqual(create.call_count, 2)

    def test_404_raises_model_error(self):
        client = self._client(GITHUB_COPILOT_TOKEN='eyJhbGciOi',
                              GITHUB_COPILOT_API_MODE='chat_completions',
                              GITHUB_COPILOT_MODEL='gpt-6-luna')

        class NotFound(Exception):
            def __init__(self):
                self.response = mock.MagicMock()
                self.response.status_code = 404

        create = mock.MagicMock(side_effect=NotFound())
        openai_client = mock.MagicMock()
        openai_client.chat.completions.create = create

        with mock.patch.object(GitHubCopilotClient, '_get_client', return_value=openai_client):
            with self.assertRaises(ValueError) as ctx:
                client.completions(messages=[{'role': 'user', 'content': 'hi'}])
        self.assertIn('gpt-6-luna', str(ctx.exception))
        self.assertIn('GITHUB_COPILOT_MODEL', str(ctx.exception))

    def test_list_models(self):
        client = self._client(GITHUB_COPILOT_TOKEN='eyJhbGciOi')
        with mock.patch('src.llm.client.github_copilot.requests.get',
                        return_value=FakeResponse(200, {'data': [{'id': 'gpt-6-luna'},
                                                                 {'id': 'gpt-4o'}]})):
            self.assertEqual(client.list_models(), ['gpt-6-luna', 'gpt-4o'])

    def test_list_models_on_failure_returns_empty(self):
        client = self._client(GITHUB_COPILOT_TOKEN='eyJhbGciOi')
        with mock.patch('src.llm.client.github_copilot.requests.get',
                        return_value=FakeResponse(403, None, 'forbidden')):
            self.assertEqual(client.list_models(), [])


if __name__ == '__main__':
    unittest.main()
