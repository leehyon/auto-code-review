"""测试不得写生产库 —— 守卫。

第一道防线（环境重定向）在 `db_guard.py`；本模块是第二道防线：
跑完整套测试后比对生产库指纹，被改动过就判失败。

为什么文件名以 `zz_` 开头：unittest 按文件名排序发现测试模块，
`zz_` 保证它在最后 —— 指纹比对必须发生在所有用例跑完之后。
（守卫类本身在模块 import 期就完成 install()，所以重定向对所有用例都生效。）
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# `discover -s tests` 时 tests/ 是顶层目录（不是包），相对 import 用不了；
# `discover -s .` 时 tests/ 是包，相对 import 可用。两种都试一遍。
try:
    from tests import db_guard
except ImportError:  # pragma: no cover —— 取决于 discover 的调用方式
    import db_guard

# import 期就把 DB 指向临时库：unittest 在发现阶段 import 全部模块，
# 之后才开始执行任何用例，所以这是最早且对所有用例生效的时机。
db_guard.install()


class ProductionDbUntouchedTest(unittest.TestCase):
    """生产库在测试期间不能被改动。"""

    def setUp(self):
        # 有些用例在 setUp 里改 DB_FILE，而 setUp 失败时 tearDown 不会执行，
        # 环境变量会泄漏到后面的用例。先把状态拿回来再断言。
        db_guard.install()

    def test_production_db_was_not_modified(self):
        """跑完整套测试后，生产库必须原封不动。

        指纹变了说明有用例直接写了生产库 —— 比如忘了给临时库。
        （另一个可能：跑测试的同时有真实 webhook 写进了同一个库。）
        """
        self.assertEqual(
            db_guard.baseline_fingerprint(),
            db_guard.production_db_fingerprint(),
            '生产库 data/data.db 在测试期间被改动了 —— '
            '有用例没隔离数据库。测试应该写临时库，'
            '临时库由 db_guard.install() 在 import 期提供。')

    def test_db_file_points_to_temporary_database(self):
        """环境变量必须指向临时库，不是生产库。"""
        self.assertEqual(os.environ.get('DB_FILE'), db_guard.TMP_DB)
        self.assertNotEqual(os.environ.get('DB_FILE'), db_guard.PRODUCTION_DB)

    def test_temporary_db_has_same_schema_as_production(self):
        """临时库要和生产库表结构一致 —— 不一致会让 PRAGMA 类断言假失败。"""
        self.assertEqual(db_guard.tables_of(db_guard.TMP_DB),
                         db_guard.tables_of(db_guard.PRODUCTION_DB))


if __name__ == '__main__':
    unittest.main()
