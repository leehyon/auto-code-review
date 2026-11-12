"""审查失败的处理：写库留痕、LLM 层自动重试、Dashboard 重试入口。

失败时**不在 PR 上留评论**，但会进数据库，Dashboard 上能看到并点重试。
"""
import os
import sys
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class LlmRetryTest(unittest.TestCase):
    """超时/网络错误自动重试一次；4xx 不重试。"""

    def _client(self, responses):
        from src.llm.client.github_copilot import GitHubCopilotClient
        client = GitHubCopilotClient(api_key='fake-token')
        client.api_key = 'gho_fake'
        client.max_retries = 1
        client.retry_backoff = 0
        client._copilot_token = 'gho_fake'
        client._copilot_token_expires_at = 9e18
        calls = []

        def fake(messages, model):
            calls.append(model)
            outcome = responses.pop(0)
            if isinstance(outcome, Exception):
                raise outcome
            return outcome

        with patch.object(client, '_completions_responses', side_effect=fake), \
                patch.object(client, '_completions_chat', side_effect=fake):
            yield client, calls

    def test_timeout_is_retried_once(self):
        import requests
        from src.llm.client.github_copilot import GitHubCopilotClient
        client = GitHubCopilotClient(api_key='gho_fake')
        client.max_retries = 1
        client.retry_backoff = 0
        client._copilot_token = 'gho_fake'
        client._copilot_token_expires_at = 9e18
        calls = []

        def fake(messages, model):
            calls.append(model)
            if len(calls) == 1:
                raise requests.exceptions.ReadTimeout('read timed out')
            return 'OK'

        with patch.object(client, '_completions_responses', side_effect=fake):
            result = client.completions(messages=[{'role': 'user', 'content': 'x'}])
        self.assertEqual(result, 'OK')
        self.assertEqual(len(calls), 2, '超时后应该重试一次')

    def test_second_failure_propagates(self):
        import requests
        from src.llm.client.github_copilot import GitHubCopilotClient
        client = GitHubCopilotClient(api_key='gho_fake')
        client.max_retries = 1
        client.retry_backoff = 0
        client._copilot_token = 'gho_fake'
        client._copilot_token_expires_at = 9e18
        calls = []

        def fake(messages, model):
            calls.append(model)
            raise requests.exceptions.ReadTimeout('read timed out')

        with patch.object(client, '_completions_responses', side_effect=fake):
            with self.assertRaises(requests.exceptions.ReadTimeout):
                client.completions(messages=[{'role': 'user', 'content': 'x'}])
        self.assertEqual(len(calls), 2, '只重试一次，不应该无限重试')

    def test_no_retry_when_disabled(self):
        import requests
        from src.llm.client.github_copilot import GitHubCopilotClient
        client = GitHubCopilotClient(api_key='gho_fake')
        client.max_retries = 0
        client.retry_backoff = 0
        client._copilot_token = 'gho_fake'
        client._copilot_token_expires_at = 9e18
        calls = []

        def fake(messages, model):
            calls.append(model)
            raise requests.exceptions.ReadTimeout('read timed out')

        with patch.object(client, '_completions_responses', side_effect=fake):
            with self.assertRaises(requests.exceptions.ReadTimeout):
                client.completions(messages=[{'role': 'user', 'content': 'x'}])
        self.assertEqual(len(calls), 1)

    def test_value_error_is_not_retried(self):
        """4xx（模型不存在、token 失效）重试没有意义。"""
        from src.llm.client.github_copilot import GitHubCopilotClient
        client = GitHubCopilotClient(api_key='gho_fake')
        client.max_retries = 1
        client.retry_backoff = 0
        client._copilot_token = 'gho_fake'
        client._copilot_token_expires_at = 9e18
        calls = []

        def fake(messages, model):
            calls.append(model)
            raise ValueError('model not found')

        with patch.object(client, '_completions_responses', side_effect=fake):
            with self.assertRaises(ValueError):
                client.completions(messages=[{'role': 'user', 'content': 'x'}])
        self.assertEqual(len(calls), 1, '非网络错误不该重试')

    def test_exchange_failure_is_cached_process_wide(self):
        """exchange 失败是进程级结论，不该每次审查都重试。"""
        import src.llm.client.github_copilot as mod
        original = mod._EXCHANGE_UNAVAILABLE
        mod.reset_exchange_cache()
        try:
            client = mod.GitHubCopilotClient(api_key='gho_fake')
            client._exchange_token = MagicMock(return_value=None)
            with patch.dict(os.environ, {'GITHUB_COPILOT_TOKEN': 'gho_fake'}):
                client._resolve_token()
                self.assertTrue(mod._EXCHANGE_UNAVAILABLE, '第一次失败后应记住结论')

                # 第二个 client（模拟下一次审查）不应再发起 exchange
                client2 = mod.GitHubCopilotClient(api_key='gho_fake')
                client2._exchange_token = MagicMock(return_value=None)
                client2._resolve_token()
                client2._exchange_token.assert_not_called()
        finally:
            mod._EXCHANGE_UNAVAILABLE = original


class FailedReviewRecordTest(unittest.TestCase):
    """失败时写库留痕，不在 PR 上留评论。"""

    def test_record_failed_review_writes_a_failed_row(self):
        import tempfile
        from src.service.db_factory import DatabaseFactory
        from src.service.review_service import ReviewService

        tmpdir = tempfile.mkdtemp()
        db_file = os.path.join(tmpdir, 'test.db')
        original = os.environ.copy()
        os.environ.update({'DB_TYPE': 'sqlite', 'DB_FILE': db_file})
        try:
            DatabaseFactory.reload_config()
            ReviewService.init_db()

            from src.queue.worker import _record_failed_review
            _record_failed_review('mr', {
                'project_name': 'PROJ',
                'author': 'alice',
                'source_branch': 'feat',
                'target_branch': 'dev',
                'commits': [],
                'additions': 10,
                'deletions': 2,
                'url': 'https://bb.example.com/projects/P/repos/R/pull-requests/1/overview',
                'webhook_data': {'eventKey': 'pr:opened', 'marker': 'payload'},
                'url_slug': 'bitbucket',
                'platform': 'bitbucket',
            }, '模型响应超时')

            record = ReviewService.get_review_log('mr', 1)
            self.assertIsNotNone(record, '失败记录应该写进数据库')
            self.assertEqual(record['status'], 'failed')
            self.assertEqual(record['platform'], 'bitbucket')
            self.assertIn('超时', record['error_message'])
            self.assertEqual(record['webhook_data'].get('marker'), 'payload',
                             '触发 payload 要存下来，重试靠它还原现场')
        finally:
            os.environ.clear()
            os.environ.update(original)
            DatabaseFactory.reload_config()
            import shutil
            shutil.rmtree(tmpdir, ignore_errors=True)

    def test_record_failed_review_survives_missing_context(self):
        """上下文缺失时也不能抛异常——失败记录本身写不进去就失去重试入口。"""
        from src.queue.worker import _record_failed_review
        # 不该抛
        _record_failed_review('mr', {}, '某个错误')


class RetryDispatchTest(unittest.TestCase):
    """重试分发。"""

    def test_handlers_cover_all_platforms(self):
        from src.queue.worker import _retry_handlers
        handlers = _retry_handlers()
        for platform in ('bitbucket', 'github', 'gitlab', 'gitea'):
            self.assertIn(('mr', platform), handlers)
            self.assertIn(('push', platform), handlers)

    def test_unknown_platform_is_rejected(self):
        from src.queue.worker import retry_failed_review
        with patch('src.queue.worker.ReviewService.get_review_log',
                   return_value={'webhook_data': {'x': 1}, 'platform': 'unknown'}):
            result = retry_failed_review('mr', 1)
        self.assertFalse(result['success'])
        self.assertIn('暂不支持', result['message'])

    def test_missing_record_is_rejected(self):
        from src.queue.worker import retry_failed_review
        with patch('src.queue.worker.ReviewService.get_review_log', return_value=None):
            result = retry_failed_review('mr', 999)
        self.assertFalse(result['success'])

    def test_missing_payload_is_rejected(self):
        from src.queue.worker import retry_failed_review
        with patch('src.queue.worker.ReviewService.get_review_log',
                   return_value={'webhook_data': {}, 'platform': 'bitbucket'}):
            result = retry_failed_review('mr', 1)
        self.assertFalse(result['success'])
        self.assertIn('无法重试', result['message'])

    def test_retry_keeps_row_when_replay_produces_nothing(self):
        """重放没有产出任何记录时，失败行必须保留。

        回归：早先的实现是「先删失败行 -> 再重放」，而重放走「没有变更」
        这种正常返回路径（不抛异常）时既没写新记录、旧记录也被删了，
        失败记录会凭空消失，用户再也点不到重试。
        """
        from src.queue import worker
        fake_handler = MagicMock(side_effect=lambda payload: None)  # 正常返回，不写记录

        with patch.object(worker, 'ReviewService') as service, \
                patch.dict(worker.__dict__, {'_retry_handlers': lambda: {('mr', 'bitbucket'): fake_handler}}):
            service.get_review_log.return_value = {
                'webhook_data': {'eventKey': 'pr:opened'}, 'platform': 'bitbucket',
                'error_message': '模型响应超时'}
            service.get_newest_review_log_id.return_value = 7   # 没有新记录
            result = worker.retry_failed_review('mr', 7)

        self.assertFalse(result['success'])
        service.delete_review_log.assert_not_called()
        service.update_review_log.assert_called_once()
        args = service.update_review_log.call_args[0]
        self.assertEqual(args[0], 'mr')
        self.assertEqual(args[1], 7)
        self.assertEqual(args[2], 'failed')
        self.assertIn('仍未产生审查结果', args[3])
        self.assertIn('模型响应超时', args[3], '原始失败原因要保留')

    def test_retry_deletes_old_row_when_new_record_written(self):
        """重放产出新记录时，删掉旧的失败行，Dashboard 上不留重复。"""
        from src.queue import worker
        replayed = []
        fake_handler = MagicMock(side_effect=lambda payload: replayed.append(payload))

        with patch.object(worker, 'ReviewService') as service, \
                patch.dict(worker.__dict__, {'_retry_handlers': lambda: {('mr', 'bitbucket'): fake_handler}}):
            service.get_review_log.return_value = {
                'webhook_data': {'eventKey': 'pr:opened'}, 'platform': 'bitbucket'}
            service.get_newest_review_log_id.return_value = 8   # 有新记录
            result = worker.retry_failed_review('mr', 7)

        self.assertTrue(result['success'])
        self.assertEqual(len(replayed), 1, '应该重放一次')
        service.delete_review_log.assert_called_once_with('mr', 7)

    def test_retry_keeps_row_when_handler_raises(self):
        """重放抛异常（异常路径会写失败记录）时，行为与正常产出新记录一致。"""
        from src.queue import worker
        fake_handler = MagicMock(side_effect=RuntimeError('boom'))

        with patch.object(worker, 'ReviewService') as service, \
                patch.dict(worker.__dict__, {'_retry_handlers': lambda: {('mr', 'bitbucket'): fake_handler}}):
            service.get_review_log.return_value = {
                'webhook_data': {'eventKey': 'pr:opened'}, 'platform': 'bitbucket'}
            service.get_newest_review_log_id.return_value = 8
            result = worker.retry_failed_review('mr', 7)

        self.assertTrue(result['success'])
        service.delete_review_log.assert_called_once_with('mr', 7)


class ReviewLogStatusTest(unittest.TestCase):
    """查询要带上 status / error_message / platform。"""

    def setUp(self):
        import tempfile
        self.tmpdir = tempfile.mkdtemp()
        self.db_file = os.path.join(self.tmpdir, 'test.db')
        self.original = os.environ.copy()
        os.environ.update({'DB_TYPE': 'sqlite', 'DB_FILE': self.db_file})
        from src.service.db_factory import DatabaseFactory
        DatabaseFactory.reload_config()
        from src.service.review_service import ReviewService
        ReviewService.init_db()

    def tearDown(self):
        from src.service.db_factory import DatabaseFactory
        os.environ.clear()
        os.environ.update(self.original)
        DatabaseFactory.reload_config()
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_get_logs_includes_status(self):
        from src.service.review_service import ReviewService
        df = ReviewService.get_mr_review_logs()
        for column in ('status', 'error_message', 'platform'):
            self.assertIn(column, df.columns)

    def test_update_review_log_sets_status(self):
        from src.service.review_service import ReviewService
        from src.entity.review_entity import MergeRequestReviewEntity
        ReviewService.insert_mr_review_log(MergeRequestReviewEntity(
            project_name='P', author='a', source_branch='s', target_branch='t',
            updated_at=1, commits=[{'message': 'm'}], score=0, url='u',
            review_result='', url_slug='s', webhook_data={}, additions=0, deletions=0,
            status='failed', error_message='超时', platform='bitbucket'))
        record = ReviewService.get_review_log('mr', 1)
        self.assertEqual(record['status'], 'failed')

        ReviewService.update_review_log('mr', 1, 'success', '', score=88)
        record = ReviewService.get_review_log('mr', 1)
        self.assertEqual(record['status'], 'success')
        self.assertEqual(record['score'], 88)

    def test_delete_review_log(self):
        from src.service.review_service import ReviewService
        from src.entity.review_entity import MergeRequestReviewEntity
        ReviewService.insert_mr_review_log(MergeRequestReviewEntity(
            project_name='P', author='a', source_branch='s', target_branch='t',
            updated_at=1, commits=[{'message': 'm'}], score=0, url='u',
            review_result='', url_slug='s', webhook_data={}, additions=0, deletions=0,
            status='failed', error_message='x', platform='bitbucket'))
        self.assertIsNotNone(ReviewService.get_review_log('mr', 1))
        ReviewService.delete_review_log('mr', 1)
        self.assertIsNone(ReviewService.get_review_log('mr', 1))


class PromptLoggingTest(unittest.TestCase):
    """日志不能打印整个 prompt。"""

    def test_call_llm_logs_only_sizes(self):
        import logging
        from src.utils.code_reviewer import CodeReviewer

        records = []

        class Handler(logging.Handler):
            def emit(self, record):
                records.append(record.getMessage())

        reviewer = CodeReviewer.__new__(CodeReviewer)
        reviewer.client = MagicMock()
        reviewer.client.completions.return_value = '## Summary\n- Review Score: 90'

        from src.utils.log import logger as project_logger
        handler = Handler()
        project_logger.addHandler(handler)
        try:
            reviewer.call_llm([{'role': 'user', 'content': 'SECRET_CONTENT_MARKER'}])
        finally:
            project_logger.removeHandler(handler)

        info_logs = [r for r in records if 'SECRET_CONTENT_MARKER' in r]
        self.assertEqual(info_logs, [], 'INFO 级别不能出现完整 prompt 内容')
        self.assertTrue(any('向 AI 发送代码 Review 请求' in r for r in records))


if __name__ == "__main__":
    unittest.main()
