"""作者名归整：Bitbucket 的「名.姓」形式统一成短名。

背景（真实问题）：
    Dashboard 的「人员提交次数」里，yingchao.han 和 yingchao 是同一个人，
    却统计成两项；作者下拉框里也是两条。同一个项目别的平台用短名，
    同一个团队里两种写法混着，统计就没法看了。

规则：取第一个点之前的部分（yingchao.han -> yingchao）。

关键点：归整必须同时作用于「写入」和「筛选/显示」两侧，语义还得完全一致。
    - 写入侧归整：以后新记录直接存短名，库里的数据慢慢收敛
    - 筛选侧归整：库里还留着归整前的老数据（yingchao.han），
      下拉框给的是归整后的名字（yingchao），不归整就查不到那些老记录
    - 显示侧归整：老记录显示出来的名字要和下拉框里的一致

如果只在一侧归整，就会出现「显示是 yingchao，筛它却查不到」这种错位。
"""
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class NormalizeAuthorTest(unittest.TestCase):
    """归整函数本身的规则。"""

    def test_drops_surname(self):
        from src.entity.review_entity import normalize_author
        self.assertEqual(normalize_author('yingchao.han'), 'yingchao')

    def test_short_name_unchanged(self):
        from src.entity.review_entity import normalize_author
        self.assertEqual(normalize_author('liyahong'), 'liyahong')
        self.assertEqual(normalize_author('keviyuan'), 'keviyuan')

    def test_only_first_dot_matters(self):
        from src.entity.review_entity import normalize_author
        self.assertEqual(normalize_author('first.last.more'), 'first')

    def test_leading_dot_kept(self):
        """开头就是点的不能归成空串 —— 那会让作者变成匿名。"""
        from src.entity.review_entity import normalize_author
        self.assertEqual(normalize_author('.hidden'), '.hidden')

    def test_non_string_untouched(self):
        """None / NaN 原样返回：空串会在下拉框里变成一个空选项。"""
        from src.entity.review_entity import normalize_author
        self.assertIsNone(normalize_author(None))
        self.assertEqual(normalize_author(''), '')


class EntityAuthorTest(unittest.TestCase):
    """入库前就归整，新记录直接存短名。"""

    def _mr(self, author):
        from src.entity.review_entity import MergeRequestReviewEntity
        return MergeRequestReviewEntity(
            project_name='p', author=author, source_branch='s', target_branch='t',
            updated_at=1, commits=[], score=1, url='u', review_result='r',
            url_slug='x', webhook_data={}, additions=0, deletions=0)

    def _push(self, author):
        from src.entity.review_entity import PushReviewEntity
        return PushReviewEntity(
            project_name='p', author=author, branch='b', updated_at=1, commits=[],
            score=1, review_result='r', url_slug='x', webhook_data={},
            additions=0, deletions=0)

    def test_mr_entity_normalizes(self):
        self.assertEqual(self._mr('yingchao.han').author, 'yingchao')

    def test_push_entity_normalizes(self):
        self.assertEqual(self._push('yingchao.han').author, 'yingchao')

    def test_short_name_unchanged(self):
        self.assertEqual(self._mr('liyahong').author, 'liyahong')


class AuthorFilterTest(unittest.TestCase):
    """筛选和显示两侧都要归整，且语义一致。

    库里的老数据是归整前写的（yingchao.han），
    下拉框给的是归整后的名字（yingchao）——
    筛选不归整就查不到老记录，显示不归整就和下拉框对不上。
    """

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.db_file = os.path.join(self.tmpdir, 'test.db')
        self.original = os.environ.copy()
        os.environ.update({'DB_TYPE': 'sqlite', 'DB_FILE': self.db_file})
        from src.service.db_factory import DatabaseFactory
        DatabaseFactory.reload_config()
        from src.service.review_service import ReviewService
        ReviewService.init_db()
        self.service = ReviewService

        # 直接写库，绕开 entity 的归整 —— 模拟归整功能上线前的老数据
        from src.service.db_factory import get_db_connection
        with get_db_connection() as conn:
            conn.execute(
                "INSERT INTO mr_review_log (project_name, author, source_branch, "
                "target_branch, updated_at, commit_messages, score, url, "
                "review_result, additions, deletions) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                ('p', 'yingchao.han', 's', 't', 1, 'c', 90, 'u', 'r', 0, 0))
            conn.commit()

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self.original)
        from src.service.db_factory import DatabaseFactory
        DatabaseFactory.reload_config()
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_legacy_row_displays_normalized(self):
        """老数据显示归整后的名字。"""
        df = self.service.get_mr_review_logs()
        self.assertEqual(list(df['author']), ['yingchao'])

    def test_filter_by_normalized_name_finds_legacy_row(self):
        """按归整后的名字筛选，能查到归整前写的老记录。"""
        df = self.service.get_mr_review_logs(authors=['yingchao'])
        self.assertEqual(len(df), 1, '按归整后的名字筛选查不到老记录')

    def test_filter_by_legacy_name_also_works(self):
        """按老名字筛选也要能查到 —— 免得用户从旧链接进来是空的。"""
        df = self.service.get_mr_review_logs(authors=['yingchao.han'])
        self.assertEqual(len(df), 1)

    def test_unrelated_name_finds_nothing(self):
        df = self.service.get_mr_review_logs(authors=['someone-else'])
        self.assertEqual(len(df), 0)

    def test_sql_and_python_agree(self):
        """SQL 里的归整表达式和 Python 函数必须一致。

        两边不一致的话，会出现「显示是 yingchao，筛它却查不到」。
        """
        import sqlite3
        from src.entity.review_entity import normalize_author

        expr = self.service._author_normalized_sql()
        conn = sqlite3.connect(self.db_file)
        try:
            rows = conn.execute(
                f"SELECT author, {expr} FROM mr_review_log").fetchall()
        finally:
            conn.close()

        self.assertTrue(rows, '测试库里应该有数据')
        for raw, sql_normalized in rows:
            self.assertEqual(sql_normalized, normalize_author(raw),
                             f'SQL 归整({raw})={sql_normalized!r} 和 '
                             f'Python 归整({raw})={normalize_author(raw)!r} 不一致')


if __name__ == '__main__':
    unittest.main()
