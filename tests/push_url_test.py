"""各平台 push 事件的 head commit 链接（Dashboard 的提交链接列用）。

要点：payload 里的 commits[].url 语义不统一——
GitHub 给的是 api.github.com，GitLab 给的是 web 地址，Gitea 给的是 api/v1，
只有 Bitbucket 需要自己拼。所以每个平台都在自己的 handler 里构造 web URL。
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class BitbucketPushUrlTest(unittest.TestCase):

    def _handler(self, payload):
        from src.bitbucket.webhook_handler import PushHandler
        return PushHandler(payload, 'token', 'https://bb.example.com')

    def test_uses_to_hash_of_branch_change(self):
        payload = {
            'eventKey': 'repo:refs_changed',
            'repository': {'slug': 'sw-repo', 'name': 'SW', 'project': {'key': 'ICCC'}},
            'actor': {'name': 'alice'},
            'changes': [{
                'ref': {'type': 'BRANCH', 'displayId': 'dev'},
                'toHash': 'a1b2c3d4e5f6',
                'fromHash': '000000000000',
            }],
        }
        url = self._handler(payload).push_url
        self.assertEqual(url,
                         'https://bb.example.com/projects/ICCC/repos/sw-repo/commits/a1b2c3d4e5f6')

    def test_skips_tag_changes(self):
        payload = {
            'eventKey': 'repo:refs_changed',
            'repository': {'slug': 'sw-repo', 'project': {'key': 'ICCC'}},
            'changes': [
                {'ref': {'type': 'TAG', 'displayId': 'v1.0'}, 'toHash': 'taghash'},
                {'ref': {'type': 'BRANCH', 'displayId': 'dev'}, 'toHash': 'branchhash'},
            ],
        }
        self.assertTrue(self._handler(payload).push_url.endswith('/commits/branchhash'))

    def test_no_branch_change_returns_empty(self):
        payload = {
            'eventKey': 'repo:refs_changed',
            'repository': {'slug': 'sw-repo', 'project': {'key': 'ICCC'}},
            'changes': [],
        }
        self.assertEqual(self._handler(payload).push_url, '')


class GitHubPushUrlTest(unittest.TestCase):

    def _handler(self, payload):
        from src.github.webhook_handler import PushHandler
        return PushHandler(payload, 'token', 'https://api.github.com')

    def test_builds_web_url_from_html_url(self):
        payload = {
            'ref': 'refs/heads/main',
            'repository': {'full_name': 'org/repo', 'html_url': 'https://github.com/org/repo'},
            'after': 'deadbeef',
            'head_commit': {'id': 'deadbeef'},
            'commits': [],
        }
        self.assertEqual(self._handler(payload).push_url,
                         'https://github.com/org/repo/commit/deadbeef')

    def test_falls_back_to_after(self):
        payload = {
            'ref': 'refs/heads/main',
            'repository': {'full_name': 'org/repo', 'html_url': 'https://github.com/org/repo/'},
            'after': 'cafebabe',
            'commits': [],
        }
        self.assertEqual(self._handler(payload).push_url,
                         'https://github.com/org/repo/commit/cafebabe')

    def test_missing_html_url_returns_empty(self):
        payload = {
            'ref': 'refs/heads/main',
            'repository': {'full_name': 'org/repo'},
            'after': 'deadbeef',
            'commits': [],
        }
        self.assertEqual(self._handler(payload).push_url, '')


class GitLabPushUrlTest(unittest.TestCase):

    def _handler(self, payload):
        from src.gitlab.webhook_handler import PushHandler
        return PushHandler(payload, 'token', 'https://gitlab.example.com')

    def test_uses_checkout_url(self):
        payload = {
            'event_name': 'push',
            'ref': 'refs/heads/main',
            'project': {'id': 7},
            'checkout_url': 'https://gitlab.example.com/org/repo/-/commit/abc123',
            'commits': [],
        }
        self.assertEqual(self._handler(payload).push_url,
                         'https://gitlab.example.com/org/repo/-/commit/abc123')

    def test_falls_back_to_last_commit_url(self):
        payload = {
            'event_name': 'push',
            'ref': 'refs/heads/main',
            'project': {'id': 7},
            'commits': [
                {'url': 'https://gitlab.example.com/org/repo/-/commit/first'},
                {'url': 'https://gitlab.example.com/org/repo/-/commit/last'},
            ],
        }
        self.assertEqual(self._handler(payload).push_url,
                         'https://gitlab.example.com/org/repo/-/commit/last')

    def test_no_info_returns_empty(self):
        payload = {'event_name': 'push', 'ref': 'refs/heads/main',
                   'project': {'id': 7}, 'commits': []}
        self.assertEqual(self._handler(payload).push_url, '')


class GiteaPushUrlTest(unittest.TestCase):

    def _handler(self, payload):
        from src.gitea.webhook_handler import PushHandler
        return PushHandler(payload, 'token', 'https://gitea.example.com')

    def test_builds_web_url_from_html_url(self):
        payload = {
            'ref': 'refs/heads/main',
            'repository': {'full_name': 'org/repo', 'html_url': 'https://gitea.example.com/org/repo'},
            'after': '1234abcd',
            'commits': [],
        }
        self.assertEqual(self._handler(payload).push_url,
                         'https://gitea.example.com/org/repo/commit/1234abcd')

    def test_falls_back_to_head_commit(self):
        payload = {
            'ref': 'refs/heads/main',
            'repository': {'full_name': 'org/repo', 'html_url': 'https://gitea.example.com/org/repo'},
            'head_commit': {'id': 'headsha'},
            'commits': [],
        }
        self.assertEqual(self._handler(payload).push_url,
                         'https://gitea.example.com/org/repo/commit/headsha')

    def test_missing_html_url_returns_empty(self):
        payload = {
            'ref': 'refs/heads/main',
            'repository': {'full_name': 'org/repo'},
            'after': '1234abcd',
            'commits': [],
        }
        self.assertEqual(self._handler(payload).push_url, '')


class PushEntityUrlTest(unittest.TestCase):
    """实体要能把链接带出去。"""

    def test_entity_keeps_url(self):
        from src.entity.review_entity import PushReviewEntity
        entity = PushReviewEntity(
            project_name='P', author='a', branch='dev', updated_at=1,
            commits=[{'message': 'm'}], score=90, review_result='r',
            url_slug='slug', webhook_data={}, additions=1, deletions=2,
            url='https://example.com/commits/abc')
        self.assertEqual(entity.url, 'https://example.com/commits/abc')

    def test_url_defaults_to_empty(self):
        from src.entity.review_entity import PushReviewEntity
        entity = PushReviewEntity(
            project_name='P', author='a', branch='dev', updated_at=1,
            commits=[{'message': 'm'}], score=90, review_result='r',
            url_slug='slug', webhook_data={}, additions=1, deletions=2)
        self.assertEqual(entity.url, '')

    def test_entity_keeps_all_other_fields(self):
        """加 url 参数不能弄丢原有字段。"""
        from src.entity.review_entity import PushReviewEntity
        entity = PushReviewEntity(
            project_name='P', author='a', branch='dev', updated_at=123,
            commits=[{'message': 'm'}], score=90, review_result='r',
            url_slug='slug', webhook_data={'k': 1}, additions=7, deletions=8)
        self.assertEqual(entity.project_name, 'P')
        self.assertEqual(entity.author, 'a')
        self.assertEqual(entity.branch, 'dev')
        self.assertEqual(entity.updated_at, 123)
        self.assertEqual(entity.commits, [{'message': 'm'}])
        self.assertEqual(entity.score, 90)
        self.assertEqual(entity.review_result, 'r')
        self.assertEqual(entity.url_slug, 'slug')
        self.assertEqual(entity.webhook_data, {'k': 1})
        self.assertEqual(entity.additions, 7)
        self.assertEqual(entity.deletions, 8)


class PushLogSchemaTest(unittest.TestCase):
    """push_review_log 必须有 url 列，旧库靠迁移补上。"""

    def test_create_table_sql_has_url_column(self):
        from src.service.review_service import ReviewService
        sql = ReviewService._get_create_table_sql('push_review_log')
        self.assertIn('url', sql)

    def test_migration_adds_url_column(self):
        import sqlite3
        import tempfile
        from src.service.db_factory import DatabaseFactory

        tmpdir = tempfile.mkdtemp()
        db_file = os.path.join(tmpdir, 'legacy.db')
        # 模拟一个没有 url 列的旧表
        con = sqlite3.connect(db_file)
        con.executescript("""
            CREATE TABLE push_review_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                project_name TEXT, author TEXT, branch TEXT, updated_at INTEGER,
                commit_messages TEXT, score INTEGER, review_result TEXT,
                additions INTEGER, deletions INTEGER);
        """)
        con.commit()
        con.close()

        import os as _os
        env = {'DB_TYPE': 'sqlite', 'DB_FILE': db_file}
        original = _os.environ.copy()
        _os.environ.update(env)
        try:
            DatabaseFactory.reload_config()
            from src.service.review_service import ReviewService
            ReviewService.init_db()
        finally:
            _os.environ.clear()
            _os.environ.update(original)
            DatabaseFactory.reload_config()

        con = sqlite3.connect(db_file)
        cur = con.cursor()
        cur.execute('PRAGMA table_info(push_review_log)')
        columns = [r[1] for r in cur.fetchall()]
        con.close()
        self.assertIn('url', columns)

        import shutil
        shutil.rmtree(tmpdir, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
