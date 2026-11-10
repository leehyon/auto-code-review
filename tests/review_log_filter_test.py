"""Dashboard 的筛选条件在两种数据库后端上都要能用。

回归背景：MR 查询写死了 MySQL 的 %s 占位符，SQLite 只认 ?，
于是筛选条件一加就抛 "near %: syntax error"；异常被 except 吞掉后
返回空 DataFrame，页面显示"没有数据"，看起来像数据没写进去。
"""
import os
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class ReviewLogFilterTest(unittest.TestCase):
    """用临时 SQLite 库跑真实的筛选逻辑。"""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.db_file = os.path.join(self.tmpdir, 'test.db')
        # 建表 + 造数据
        import sqlite3
        con = sqlite3.connect(self.db_file)
        con.executescript("""
            CREATE TABLE mr_review_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                project_name TEXT, author TEXT, source_branch TEXT, target_branch TEXT,
                updated_at INTEGER, commit_messages TEXT, score INTEGER, url TEXT,
                review_result TEXT, additions INTEGER, deletions INTEGER);
            CREATE TABLE push_review_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                project_name TEXT, author TEXT, branch TEXT, updated_at INTEGER,
                commit_messages TEXT, score INTEGER, review_result TEXT,
                additions INTEGER, deletions INTEGER, url TEXT);
        """)
        con.executemany(
            "INSERT INTO mr_review_log (project_name, author, source_branch, target_branch,"
            " updated_at, commit_messages, score, url, review_result, additions, deletions)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            [('PROJ-A', 'alice', 'feat/x', 'dev', 1000, 'm1', 90, 'u1', 'r1', 10, 2),
             ('PROJ-A', 'bob', 'feat/y', 'dev', 2000, 'm2', 80, 'u2', 'r2', 20, 4),
             ('PROJ-B', 'alice', 'feat/z', 'dev', 3000, 'm3', 70, 'u3', 'r3', 30, 6)])
        con.executemany(
            "INSERT INTO push_review_log (project_name, author, branch, updated_at,"
            " commit_messages, score, review_result, additions, deletions)"
            " VALUES (?,?,?,?,?,?,?,?,?)",
            [('PROJ-A', 'alice', 'dev', 1500, 'p1', 95, 'pr1', 5, 1),
             ('PROJ-B', 'bob', 'feat/y', 2500, 'p2', 85, 'pr2', 6, 2)])
        con.commit()
        con.close()

        env = {'DB_TYPE': 'sqlite', 'DB_FILE': self.db_file}
        self._env = patch.dict(os.environ, env)
        self._env.start()
        # 让 db_factory 重新读配置
        from src.service.db_factory import DatabaseFactory
        DatabaseFactory.reload_config()

    def tearDown(self):
        from src.service.db_factory import DatabaseFactory
        DatabaseFactory.reload_config()
        self._env.stop()
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _mr(self, **kw):
        from src.service.review_service import ReviewService
        return ReviewService().get_mr_review_logs(**kw)

    def _push(self, **kw):
        from src.service.review_service import ReviewService
        return ReviewService().get_push_review_logs(**kw)

    def test_no_filter_returns_all(self):
        self.assertEqual(len(self._mr()), 3)

    def test_filter_by_author(self):
        df = self._mr(authors=['alice'])
        self.assertEqual(len(df), 2)
        self.assertTrue(all(a == 'alice' for a in df['author']))

    def test_filter_by_project(self):
        df = self._mr(project_names=['PROJ-B'])
        self.assertEqual(len(df), 1)
        self.assertEqual(df.iloc[0]['project_name'], 'PROJ-B')

    def test_filter_by_time_range(self):
        df = self._mr(updated_at_gte=1500, updated_at_lte=2500)
        self.assertEqual(len(df), 1)
        self.assertEqual(df.iloc[0]['updated_at'], 2000)

    def test_combined_filters(self):
        df = self._mr(authors=['alice'], project_names=['PROJ-A'],
                      updated_at_gte=0, updated_at_lte=5000)
        self.assertEqual(len(df), 1)

    def test_filters_do_not_return_empty_when_they_should_match(self):
        """核心回归：加了筛选条件查不到数据时，不能是占位符报错被吞掉。"""
        df = self._mr(authors=['alice'], updated_at_gte=0, updated_at_lte=9999)
        self.assertGreater(len(df), 0, '筛选条件生效后查不到数据')

    def test_push_filters(self):
        df = self._push(authors=['alice'])
        self.assertEqual(len(df), 1)
        df2 = self._push(updated_at_gte=2000)
        self.assertEqual(len(df2), 1)

    def test_placeholder_matches_backend(self):
        from src.service.db_factory import get_db_connection
        with get_db_connection() as conn:
            self.assertEqual(conn.placeholder, '?')


class PlaceholderStyleTest(unittest.TestCase):

    def test_sqlite_uses_qmark(self):
        from src.service.db_factory import SQLiteConnection
        self.assertEqual(SQLiteConnection.placeholder, '?')

    def test_mysql_uses_format(self):
        from src.service.db_factory import MySQLConnection
        self.assertEqual(MySQLConnection.placeholder, '%s')


if __name__ == "__main__":
    unittest.main()
