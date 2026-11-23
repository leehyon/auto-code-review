"""PR 删除后清理审查记录的测试。

PR 被删除时 Bitbucket 会发 pr:deleted 事件，Dashboard 上对应的
审查记录要跟着删掉 —— PR 都没了，记录留着只是打不开的死链，
还继续占着统计里的提交次数和代码行数。
"""

import os
import sqlite3
import tempfile
import unittest

from src.entity.review_entity import MergeRequestReviewEntity
from src.service.review_service import ReviewService
from src.utils.pr_ref import parse_bitbucket_pr_ref


class PrDeletedCleanupTest(unittest.TestCase):
    """按 pr_id / pr_repo 删除记录。"""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.db_file = os.path.join(self.tmpdir, 'test.db')
        con = sqlite3.connect(self.db_file)
        con.executescript("""
            CREATE TABLE mr_review_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                project_name TEXT, author TEXT, source_branch TEXT, target_branch TEXT,
                updated_at INTEGER, commit_messages TEXT, score INTEGER, url TEXT,
                review_result TEXT, additions INTEGER, deletions INTEGER,
                status TEXT DEFAULT 'success', error_message TEXT, platform TEXT,
                webhook_data TEXT, author_sole_committer INTEGER DEFAULT 1,
                pr_id TEXT, pr_repo TEXT);
        """)
        con.executemany(
            "INSERT INTO mr_review_log (project_name, author, source_branch, target_branch,"
            " updated_at, commit_messages, score, url, review_result, additions, deletions,"
            " pr_id, pr_repo) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            [('PROJ-A', 'alice', 'feat/x', 'dev', 1000, 'm1', 90, 'u1', 'r1', 10, 2, '91', 'repo-a'),
             ('PROJ-A', 'bob', 'feat/y', 'dev', 2000, 'm2', 80, 'u2', 'r2', 20, 4, '92', 'repo-a'),
             ('PROJ-B', 'alice', 'feat/z', 'dev', 3000, 'm3', 70, 'u3', 'r3', 30, 6, '91', 'repo-b')])
        con.commit()
        con.close()

        # 切到临时库
        self.original_env = os.environ.copy()
        os.environ['DB_TYPE'] = 'sqlite'
        os.environ['DB_FILE'] = self.db_file
        from src.service.db_factory import DatabaseFactory
        DatabaseFactory.reload_config()

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self.original_env)
        from src.service.db_factory import DatabaseFactory
        DatabaseFactory.reload_config()

    def _rows(self):
        con = sqlite3.connect(self.db_file)
        rows = con.execute('SELECT id, pr_id, pr_repo FROM mr_review_log ORDER BY id').fetchall()
        con.close()
        return rows

    def test_deletes_only_the_matching_pr_and_repo(self):
        """同一个 pr_id 在不同仓库下要分开处理。"""
        deleted = ReviewService.delete_mr_review_log_by_pr('91', 'repo-a')
        self.assertEqual(deleted, 1)
        rows = self._rows()
        self.assertEqual([r[0] for r in rows], [2, 3])

    def test_does_not_delete_other_repo_with_same_pr_id(self):
        """repo-b 的 PR 91 不能被 repo-a 的删除事件带走。"""
        ReviewService.delete_mr_review_log_by_pr('91', 'repo-a')
        remaining = [r for r in self._rows() if r[1] == '91']
        self.assertEqual(len(remaining), 1)
        self.assertEqual(remaining[0][2], 'repo-b')

    def test_deletes_without_repo_matches_any_repo(self):
        """不传仓库时按 pr_id 删，两个仓库的 91 都没了。"""
        deleted = ReviewService.delete_mr_review_log_by_pr('91', '')
        self.assertEqual(deleted, 2)

    def test_unknown_pr_deletes_nothing(self):
        deleted = ReviewService.delete_mr_review_log_by_pr('999', 'repo-a')
        self.assertEqual(deleted, 0)
        self.assertEqual(len(self._rows()), 3)

    def test_empty_pr_id_deletes_nothing(self):
        """空 id 不能变成「删光所有」。"""
        deleted = ReviewService.delete_mr_review_log_by_pr('', 'repo-a')
        self.assertEqual(deleted, 0)
        self.assertEqual(len(self._rows()), 3)

    def test_none_pr_id_deletes_nothing(self):
        # 少一层判断就会把 None 变成「删光所有」，所以这里明确挡住
        self.assertEqual(ReviewService.delete_mr_review_log_by_pr(None, 'repo-a'), 0)
        self.assertEqual(len(self._rows()), 3)


class EntityPrRefTest(unittest.TestCase):
    """实体要带上 PR 的 id 和仓库 slug。"""

    def _entity(self, pr_id='91', pr_repo='repo-a'):
        return MergeRequestReviewEntity(
            project_name='proj', author='alice', source_branch='feat/x',
            target_branch='dev', updated_at=1, commits=[], score=90,
            url='http://x/pull-requests/91', review_result='ok', url_slug='slug',
            webhook_data={}, additions=10, deletions=2,
            pr_id=pr_id, pr_repo=pr_repo)

    def test_stores_pr_id_and_repo(self):
        entity = self._entity()
        self.assertEqual(entity.pr_id, '91')
        self.assertEqual(entity.pr_repo, 'repo-a')

    def test_defaults_to_empty(self):
        entity = MergeRequestReviewEntity(
            project_name='proj', author='alice', source_branch='feat/x',
            target_branch='dev', updated_at=1, commits=[], score=90,
            url='http://x', review_result='ok', url_slug='slug',
            webhook_data={}, additions=10, deletions=2)
        self.assertEqual(entity.pr_id, '')
        self.assertEqual(entity.pr_repo, '')

    def test_pr_id_is_stringified(self):
        """PR id 从 payload 里来时可能是 int，统一存字符串。"""
        self.assertEqual(self._entity(pr_id=91).pr_id, '91')


class PrRefBackfillTest(unittest.TestCase):
    """从 url 解析 PR 的 id 和仓库 slug（回填老记录用）。"""

    def test_parses_repo_and_id(self):
        repo, pr_id = parse_bitbucket_pr_ref(
            'https://host/projects/VGC/repos/sw-7165-x/pull-requests/91')
        self.assertEqual(repo, 'sw-7165-x')
        self.assertEqual(pr_id, '91')

    def test_parses_with_overview_suffix(self):
        repo, pr_id = parse_bitbucket_pr_ref(
            'https://host/projects/ICCC/repos/sw-1068/pull-requests/32/overview')
        self.assertEqual(repo, 'sw-1068')
        self.assertEqual(pr_id, '32')

    def test_returns_none_for_non_pr_url(self):
        self.assertEqual(parse_bitbucket_pr_ref('https://host/projects/x/repos/y'), (None, None))
        self.assertEqual(parse_bitbucket_pr_ref(''), (None, None))
        self.assertEqual(parse_bitbucket_pr_ref(None), (None, None))


if __name__ == '__main__':
    unittest.main()
