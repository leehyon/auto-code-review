"""测试不得写生产库 —— 公共逻辑。

背景（真实事故）：
    跑一次单元测试，Dashboard 的 Merge Request 列表里多出两条空记录
    （项目名/作者/链接全空、status=failed、error_message="某个错误"）。

原因：`tests/review_retry_test.py` 有个用例调 `_record_failed_review('mr', {}, '某个错误')`
写失败记录，但它没给临时库，于是写进了 `data/data.db` —— 那是生产库。
而 `data/` 在 docker compose 里是 volume 挂载，容器内跑测试改的正是线上数据。

两道防线：
    1. `install()`：把 DB_FILE 指向临时库，忘了隔离的用例最多脏副本
    2. `zz_production_db_guard_test.py`：跑完比对生产库指纹，被改动过就判失败

临时库是「只有表结构、没有数据」的副本：
    - 表结构要和生产一致 —— `insert_placeholder_test.py` 用 PRAGMA 核对真实列
    - 不要生产数据 —— 有些用例断言默认库里查不到东西

本模块只用标准库：import pandas 之类的东西会让本机（缺依赖）直接 collect 失败。
"""
import atexit
import hashlib
import os
import shutil
import sqlite3
import tempfile

# 生产库（相对仓库根）。测试期间绝不允许写它。
PRODUCTION_DB = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'data', 'data.db')

# 临时库路径按 PID 定，不按 import 次数。
# 原因：tests/ 可能被当成包 import（`tests.db_guard`），也可能被当成顶层目录
# import（`db_guard`）—— 两种方式会造出两个模块对象、两个临时库。
# 固定路径 + 环境变量哨兵，保证只有一个临时库在生效。
TMP_DIR = os.path.join(tempfile.gettempdir(), f'acr_test_db_{os.getpid()}')
TMP_DB = os.path.join(TMP_DIR, 'data.db')

_installed = False
_baseline_fingerprint = None


def production_db_fingerprint():
    """生产库文件的 sha256；不存在返回 None。"""
    if not os.path.exists(PRODUCTION_DB):
        return None
    h = hashlib.sha256()
    with open(PRODUCTION_DB, 'rb') as f:
        for chunk in iter(lambda: f.read(65536), b''):
            h.update(chunk)
    return h.hexdigest()


def baseline_fingerprint():
    """install() 时记录的生产库指纹。"""
    return _baseline_fingerprint


def _clone_schema(src_path, dst_path):
    """把源库的表结构复制到目标库 —— 只有 DDL，没有数据。

    用 sqlite_master 里的原始建表语句重建，保证列定义与生产完全一致
    （测试里有人用 PRAGMA table_info 核对 INSERT 的列）。
    """
    if not os.path.exists(src_path):
        return False
    src = sqlite3.connect(src_path)
    try:
        rows = src.execute(
            "SELECT sql FROM sqlite_master "
            "WHERE sql IS NOT NULL AND type = 'table' AND name NOT LIKE 'sqlite_%'"
        ).fetchall()
    finally:
        src.close()

    dst = sqlite3.connect(dst_path)
    try:
        for (sql,) in rows:
            dst.execute(sql)
        dst.commit()
    finally:
        dst.close()
    return True


def install():
    """把 DB 指向临时库。可重复调用（幂等）。

    必须在任何用例执行之前调用。unittest 在「发现阶段」会 import 全部测试模块，
    之后才开始执行 —— 所以 import 期是最早的、且对所有用例都生效的时机。

    每次调用都重新申明环境变量：有些用例在 setUp 里改 DB_FILE，
    而 setUp 失败时 tearDown 不会执行（unittest 只在 setUp 成功后调 tearDown），
    环境变量就泄漏了。守卫用例自己调 install() 就能拿回干净的状态。
    """
    global _installed, _baseline_fingerprint
    if not _installed:
        _installed = True
        _baseline_fingerprint = production_db_fingerprint()
        os.makedirs(TMP_DIR, exist_ok=True)
        _clone_schema(PRODUCTION_DB, TMP_DB)
        atexit.register(lambda: shutil.rmtree(TMP_DIR, ignore_errors=True))

    os.environ['DB_TYPE'] = 'sqlite'
    os.environ['DB_FILE'] = TMP_DB


def tables_of(path):
    """库里的用户表名集合。"""
    if not os.path.exists(path):
        return set()
    conn = sqlite3.connect(path)
    try:
        return {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' "
            "AND name NOT LIKE 'sqlite_%'")}
    finally:
        conn.close()
