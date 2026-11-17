"""回归测试：INSERT 语句的占位符数必须等于列数。

背景：2026-11-17 的 push 审查在 Bitbucket 上已经成功回写评论，
但入库时抛了 "15 values for 14 columns" —— push 的 INSERT 从 MR
那份复制过来，多带了一个 `?`。异常被 except 吞掉只打了一行日志，
所以 Dashboard 里什么都看不到，排查了很久。

这类错误靠人眼看不出来，交给测试守。
"""
import re
import sqlite3
import unittest
from pathlib import Path

SERVICE_PY = Path(__file__).resolve().parents[1] / 'src' / 'service' / 'review_service.py'
INSERT_RE = re.compile(
    r"INSERT\s+INTO\s+(\w+)\s*\(([^)]*)\)\s*VALUES\s*\(([^)]*)\)", re.S | re.I)


class InsertPlaceholderCountTest(unittest.TestCase):
    """占位符数量校验"""

    @classmethod
    def setUpClass(cls):
        cls.source = SERVICE_PY.read_text(encoding='utf-8')

    def _inserts(self):
        out = []
        for m in INSERT_RE.finditer(self.source):
            table = m.group(1)
            cols = [c.strip() for c in m.group(2).replace('\n', ' ').split(',') if c.strip()]
            placeholders = len(re.findall(r'\?', m.group(3)))
            line = self.source[:m.start()].count('\n') + 1
            out.append((table, cols, placeholders, line))
        return out

    def test_insert_statements_found(self):
        """守卫：解析器本身要能抓到 INSERT，别因为正则失效而全绿。"""
        tables = {t for t, _, _, _ in self._inserts()}
        self.assertIn('mr_review_log', tables)
        self.assertIn('push_review_log', tables)

    def test_placeholder_count_matches_column_count(self):
        """每个 INSERT 的占位符数必须等于列数。"""
        for table, cols, placeholders, line in self._inserts():
            with self.subTest(table=table, line=line):
                self.assertEqual(
                    placeholders, len(cols),
                    f"{table} (L{line}): {len(cols)} 列却写了 {placeholders} 个占位符")

    def test_push_columns_match_actual_table(self):
        """push 的 INSERT 列必须和真实表结构一致（漏列/多列都要发现）。"""
        import sys
        sys.path.insert(0, str(SERVICE_PY.resolve().parents[2]))
        from src.service.db_factory import get_db_connection

        inserts = {t: cols for t, cols, _, _ in self._inserts()}
        with get_db_connection() as conn:
            for table, cols in inserts.items():
                rows = conn.execute(f'PRAGMA table_info({table})').fetchall()
                actual = {r[1] for r in rows}
                with self.subTest(table=table):
                    for col in cols:
                        self.assertIn(
                            col, actual,
                            f"{table}: INSERT 里的列 {col!r} 在真实表里不存在")
                    # INSERT 没写 id，所以列数应该正好是表列数减一
                    self.assertEqual(
                        len(cols), len(actual) - 1,
                        f"{table}: INSERT 列数和真实表列数对不上")


if __name__ == '__main__':
    unittest.main()
