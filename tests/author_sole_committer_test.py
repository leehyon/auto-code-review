"""代码归属判定的测试。

背景：Merge Request 的「人员代码变更总行数」和「人员平均分数」原先
一律记在提 PR 的人头上。但一个 PR 里如果有别人提交的 commit，
这份代码就不是他写的 —— 行数和评分都不该算在他名下。

口径：所有 commit 的作者都是提 PR 的人本人，才算可以归属；
只要有别人的 commit，这次的行数和评分都不计入这个人的统计。
"""

import os
import sqlite3
import tempfile
import unittest

from src.entity.review_entity import (
    MergeRequestReviewEntity,
    PushReviewEntity,
    commit_authors,
    is_author_sole_committer,
)


class CommitAuthorsTest(unittest.TestCase):
    """从 commit 列表里提取作者集合。"""

    def test_reads_author_name_field(self):
        commits = [{'author_name': 'alice'}, {'author_name': 'bob'}]
        self.assertEqual(commit_authors(commits), {'alice', 'bob'})

    def test_reads_author_field(self):
        """GitLab 的统一结构用 author，不是 author_name。"""
        commits = [{'author': 'alice'}, {'author': 'bob'}]
        self.assertEqual(commit_authors(commits), {'alice', 'bob'})

    def test_normalizes_author_names(self):
        """yingchao.han 和 yingchao 是同一个人，不能算成两个。"""
        commits = [{'author_name': 'yingchao.han'}, {'author_name': 'yingchao'}]
        self.assertEqual(commit_authors(commits), {'yingchao'})

    def test_ignores_commits_without_author(self):
        commits = [{'author_name': ''}, {'author_name': 'alice'}, {'no_author': 1}]
        self.assertEqual(commit_authors(commits), {'alice'})

    def test_empty_commit_list(self):
        self.assertEqual(commit_authors([]), set())
        self.assertEqual(commit_authors(None), set())

    def test_non_dict_entries_are_skipped(self):
        commits = ['not a dict', {'author_name': 'alice'}]
        self.assertEqual(commit_authors(commits), {'alice'})


class CodeLinesAttributableTest(unittest.TestCase):
    """判定：这次的代码行数能不能算在提 PR 的人头上。"""

    def test_all_commits_by_pr_author(self):
        commits = [{'author_name': 'alice'}, {'author_name': 'alice'}]
        self.assertTrue(is_author_sole_committer('alice', commits))

    def test_one_commit_by_someone_else(self):
        """只要有一个别人的 commit，就不归属。"""
        commits = [{'author_name': 'alice'}, {'author_name': 'bob'}]
        self.assertFalse(is_author_sole_committer('alice', commits))

    def test_all_commits_by_someone_else(self):
        """全是别人的 commit（代提 PR），同样不归属。"""
        commits = [{'author_name': 'bob'}]
        self.assertFalse(is_author_sole_committer('alice', commits))

    def test_pr_author_uses_long_name_and_commits_short(self):
        """Bitbucket 的 PR 作者是 yingchao.han，commit 作者是 yingchao —— 同一人。"""
        commits = [{'author_name': 'yingchao'}]
        self.assertTrue(is_author_sole_committer('yingchao.han', commits))

    def test_pr_author_short_and_commits_long(self):
        """反过来也要一致。"""
        commits = [{'author_name': 'yingchao.han'}]
        self.assertTrue(is_author_sole_committer('yingchao', commits))

    def test_merge_commit_author_counts(self):
        """Bitbucket 的 PR 合并提交 author 是代码捐献者，必须计入。

        排除它就等于把 camima 的代码记到提 PR 的人头上。
        """
        commits = [
            {'title': 'Pull request #88: Feature/VGC-156 lin',
             'author_name': 'camima'},
            {'title': 'fix bug', 'author_name': 'alice'},
        ]
        self.assertFalse(is_author_sole_committer('alice', commits))

    def test_no_commits_is_not_attributable(self):
        """拿不到 commit 作者就无从证明是本人 —— 不归属。"""
        self.assertFalse(is_author_sole_committer('alice', []))
        self.assertFalse(is_author_sole_committer('alice', None))

    def test_commits_without_author_is_not_attributable(self):
        commits = [{'title': 'something'}]
        self.assertFalse(is_author_sole_committer('alice', commits))


class EntityAttributionTest(unittest.TestCase):
    """实体在构造时就算好归属标记。"""

    def _mr_entity(self, author, commits):
        return MergeRequestReviewEntity(
            project_name='proj', author=author, source_branch='feature',
            target_branch='main', updated_at=1, commits=commits, score=90,
            url='http://x', review_result='ok', url_slug='slug',
            webhook_data={}, additions=100, deletions=20)

    def test_single_author_mr_is_attributable(self):
        entity = self._mr_entity('alice', [{'author_name': 'alice'}])
        self.assertTrue(entity.author_sole_committer)

    def test_multi_author_mr_is_not_attributable(self):
        entity = self._mr_entity('alice', [{'author_name': 'alice'},
                                           {'author_name': 'bob'}])
        self.assertFalse(entity.author_sole_committer)

    def test_push_entity_has_the_flag(self):
        entity = PushReviewEntity(
            project_name='proj', author='alice', branch='main', updated_at=1,
            commits=[{'author_name': 'bob'}], score=0, review_result='',
            url_slug='slug', webhook_data={}, additions=10, deletions=1)
        self.assertFalse(entity.author_sole_committer)


class StatsAttributionTest(unittest.TestCase):
    """统计端点：非本人提交的记录不进入人员均分和行数统计。

    走真实的 /api/review/stats 逻辑（用临时 SQLite 库）。
    """

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
        # alice: 一条本人提交（均分 80），一条混了别人的 commit（均分 100）
        # bob:   一条本人提交（均分 60）
        con.executemany(
            "INSERT INTO mr_review_log (project_name, author, source_branch, target_branch,"
            " updated_at, commit_messages, score, url, review_result, additions, deletions,"
            " author_sole_committer) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            [('PROJ-A', 'alice', 'f1', 'dev', 1000, 'm1', 80, 'u1', 'r1', 10, 2, 1),
             ('PROJ-A', 'alice', 'f2', 'dev', 2000, 'm2', 100, 'u2', 'r2', 500, 50, 0),
             ('PROJ-A', 'bob', 'f3', 'dev', 3000, 'm3', 60, 'u3', 'r3', 30, 6, 1)])
        con.commit()
        con.close()

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

    def _stats(self):
        """打真实的统计接口（不复制聚合逻辑 —— 复制过就漏掉过 bug）。"""
        import ui_server
        client = ui_server.ui_app.test_client()
        response = client.get('/api/review/stats?type=mr')
        self.assertEqual(response.status_code, 200)
        data = response.get_json()
        self.assertNotIn('error', data)
        scores = {r['name']: r['average_score'] for r in data['author_scores']}
        lines = {r['name']: r['code_lines'] for r in data['author_code_lines']}
        counts = {r['name']: r['count'] for r in data['author_counts']}
        return scores, lines, counts

    def test_average_score_excludes_mixed_commit_pr(self):
        """alice 的均分应该是 80，不是 (80+100)/2=90。"""
        scores, _, _ = self._stats()
        self.assertAlmostEqual(scores['alice'], 80.0)

    def test_code_lines_exclude_mixed_commit_pr(self):
        """alice 的行数应该是 12，不是 12+550=562。"""
        _, lines, _ = self._stats()
        self.assertEqual(lines['alice'], 12)

    def test_other_authors_unaffected(self):
        scores, lines, _ = self._stats()
        self.assertAlmostEqual(scores['bob'], 60.0)
        self.assertEqual(lines['bob'], 36)

    def test_commit_count_still_counts_every_record(self):
        """提交次数是「提了多少个 PR」，不区分代码是谁写的 —— 计入全部记录。"""
        _, _, counts = self._stats()
        self.assertEqual(counts['alice'], 2)

    def test_records_still_returned(self):
        """记录本身照常返回（只是不进人员统计）。"""
        from src.service.review_service import ReviewService
        df = ReviewService().get_mr_review_logs()
        self.assertEqual(len(df), 3)


if __name__ == '__main__':
    unittest.main()
