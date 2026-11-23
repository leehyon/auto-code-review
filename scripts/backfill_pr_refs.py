"""回填历史记录的 pr_id / pr_repo。

这两列是 2026-11 之后加的：PR 被删除时 Bitbucket 会发 pr:deleted 事件，
靠它们定位要删的记录。老记录没有这两列，PR 删了就删不掉。

老记录的 url 里带着 PR 的 id 和仓库 slug，可以直接解析出来补上。
解析逻辑在 src/utils/pr_ref.py（脚本和测试共用一份）。

用法：
    python scripts/backfill_pr_refs.py --dry-run
    python scripts/backfill_pr_refs.py
"""

import argparse
import os
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.utils.pr_ref import parse_bitbucket_pr_ref  # noqa: E402

DB_PATH = os.environ.get('DB_FILE', '/app/data/data.db')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--dry-run', action='store_true', help='只打印不写库')
    args = parser.parse_args()

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        'SELECT id, url, pr_id, pr_repo FROM mr_review_log').fetchall()

    changed = 0
    unchanged = 0
    skipped = 0
    for row in rows:
        repo, pr_id = parse_bitbucket_pr_ref(row['url'])
        if not repo or not pr_id:
            skipped += 1
            print(f"  id={row['id']}: url 里没有 PR 信息，跳过 ({row['url']})")
            continue
        if row['pr_id'] == pr_id and row['pr_repo'] == repo:
            unchanged += 1
            continue
        changed += 1
        print(f"  id={row['id']}: pr_id={row['pr_id']!r} -> {pr_id!r}, "
              f"pr_repo={row['pr_repo']!r} -> {repo!r}")
        if not args.dry_run:
            conn.execute(
                'UPDATE mr_review_log SET pr_id = ?, pr_repo = ? WHERE id = ?',
                (pr_id, repo, row['id']))

    if not args.dry_run:
        conn.commit()
    conn.close()
    print(f"\n完成：更新 {changed} 条，不变 {unchanged} 条，跳过 {skipped} 条"
          f"{'（dry-run，未写库）' if args.dry_run else ''}")


if __name__ == '__main__':
    sys.exit(main())
