"""测试包。

两件事：
    1. 让 `unittest discover -s .` 能发现 tests/ 下的用例
       （Python 3.11+ 不再自动把无 __init__.py 的目录当 namespace package）
    2. import 期就装上「测试不得写生产库」的守卫

第 2 点为什么放在这里：`discover -s tests` 时 unittest 把 tests/ 当顶层目录，
**不会 import 这个文件**。所以真正的守卫逻辑放在 `zz_production_db_guard_test.py`
（匹配 *_test.py，两种调用方式都会被导入）。这里再装一次是双保险 ——
两种 import 路径（tests.db_guard / db_guard）指向同一个 PID 固定的临时库，
install() 是幂等的。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    from tests import db_guard
except ImportError:  # pragma: no cover —— 取决于 discover 的调用方式
    import db_guard

db_guard.install()
