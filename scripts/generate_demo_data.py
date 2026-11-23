"""生成 Dashboard 演示数据（假数据），用于产出 README 截图。

**为什么需要它**：README 是公开仓库，截图里不能带真实的项目名、
人员姓名、分支名和提交信息。这个脚本造一份结构完全一致的假数据，
让 Dashboard 渲染出真实的样子，但不含任何真实信息。

**不碰生产数据**：写到独立的临时库（默认 /tmp/demo-dashboard.db），
用 DB_FILE 环境变量指过去。跑之前会删掉目标文件重建。

跑法（要在容器里跑，前端和后端共用一个 DB_FILE）：
    # 1) 造数据
    python scripts/generate_demo_data.py --db /app/data/demo.db
    # 2) 用这个库起服务（换个端口，别和正式的撞）
    DB_FILE=/app/data/demo.db UI_PORT=5005 python ui_server.py
    # 3) 截图，然后停掉这个服务

统计图保持真实的形状（每个项目的条形长度、人员分布），
只是把名字换成虚构的 —— 演示效果和真实截图一致。

随机种子固定（--seed，默认 42），所以每次生成的数据一样，
截图可以复现。
"""

import argparse
import json
import os
import random
import sqlite3
import time

# 虚构的项目名：结构像真的（缩写-平台-模块），但不对应任何真实仓库
PROJECTS = [
    'sw-2841-gsa-vw-infotainment',
    'sw-3105-gsa-vw-cluster',
    'sw-2276-cam-lcd-hmi',
    'sw-4482-gsa-vw-domain',
]

# 虚构的作者名（不用真人姓名，用常见英文占位）
AUTHORS = [
    'alex.rivera',
    'bo.chen',
    'chloe.novak',
    'dan.okafor',
    'emily.sato',
    'felix.muller',
]

BRANCHES = [
    'feature/VD-1042-add-diagnostics',
    'bugfix/VD-1103-null-pointer',
    'feature/VD-0987-update-can-stack',
    'refactor/VD-1150-split-driver',
    'feature/VD-1211-error-reporting',
    'bugfix/VD-1188-timing-violation',
]

TARGET_BRANCHES = ['develop', 'release/2.4.0', 'main']

COMMITS = [
    'feat: add diagnostic read support for the CAN stack',
    'fix: handle null pointer when the frame is empty',
    'refactor: split the driver into separate modules',
    'chore: update the version header and build metadata',
    'fix: correct the timeout calculation in the scheduler',
    'feat: report the error state over the diagnostic channel',
    'style: apply the naming convention to local variables',
    'fix: remove the redundant length check before the copy',
]

PUSH_COMMITS = [
    'fix: correct the off-by-one in the ring buffer index',
    'feat: expose the diagnostic trace over the trace channel',
    'chore: bump the module version',
]


def build_mr_records(now):
    """造 MR 审查记录。

    分布照着真实使用场景来：多数 PR 是本人提交（计入统计），
    少量混了别人的 commit（不计入人员行数和均分），
    再加一条失败的记录 —— 这样截图能展示完整的状态。
    """
    records = []
    record_id = 1
    # (项目, 作者, 提交数, 是否混入他人 commit, 分数, 状态)
    plan = [
        (0, 0, 3, False, 92, 'success'),
        (0, 0, 1, False, 78, 'success'),
        (0, 1, 2, False, 95, 'success'),
        (1, 2, 4, False, 88, 'success'),
        (1, 2, 2, True, 96, 'success'),
        (2, 3, 1, False, 61, 'success'),
        (2, 4, 3, False, 85, 'success'),
        (3, 5, 2, False, 90, 'success'),
        (3, 1, 2, True, 73, 'success'),
        (3, 0, 1, False, 88, 'success'),
        (1, 4, 2, False, 0, 'failed'),
    ]
    for project_idx, author_idx, commit_count, mixed, score, status in plan:
        project = PROJECTS[project_idx]
        author = AUTHORS[author_idx]
        branch = random.choice(BRANCHES)
        target = random.choice(TARGET_BRANCHES)
        pr_id = 100 + record_id
        updated_at = now - random.randint(3600, 21 * 24 * 3600)

        # 提交信息：混合作者的 PR 里，前面的提交属于别人
        commits = random.sample(COMMITS, min(commit_count, len(COMMITS)))
        summary = '; '.join(commits[:2])
        if len(commits) > 2:
            summary += f' +{len(commits) - 2}'

        additions = random.randint(30, 900)
        deletions = random.randint(0, 260)
        if mixed:
            # 混入他人 commit：代码量明显更大（真实场景里这类 PR 常是
            # 整体移植或大批量重构）
            additions = random.randint(3000, 28000)
            deletions = random.randint(500, 6000)

        records.append({
            'id': record_id,
            'project_name': project,
            'author': author,
            'source_branch': branch,
            'target_branch': target,
            'updated_at': updated_at,
            'commit_messages': summary,
            'score': score,
            'url': f'https://bitbucket.example.com/projects/DEMO/repos/{project}'
                   f'/pull-requests/{pr_id}',
            'review_result': '## Summary\n- Overall Risk Level: MEDIUM\n'
                             '- Review Score: 88\n',
            'additions': additions,
            'deletions': deletions,
            'status': status,
            'error_message': 'AI Code Review 服务出现未知错误: model timeout'
                             if status == 'failed' else '',
            'platform': 'bitbucket',
            'webhook_data': json.dumps({
                'eventKey': 'pr:opened',
                'pullRequest': {'id': pr_id},
            }),
            # 混了他人 commit 的记录不计入人员行数和均分
            'author_sole_committer': 0 if mixed else 1,
            'pr_id': str(pr_id),
            'pr_repo': project,
        })
        record_id += 1
    return records


def build_push_records(now):
    """造 Push 审查记录。"""
    records = []
    record_id = 1
    plan = [
        (3, 0, 89, 12, 94),
        (3, 0, 34, 6, 88),
        (0, 1, 156, 21, 91),
        (1, 3, 62, 15, 76),
    ]
    for project_idx, author_idx, additions, deletions, score in plan:
        project = PROJECTS[project_idx]
        author = AUTHORS[author_idx]
        records.append({
            'id': record_id,
            'project_name': project,
            'author': author,
            'branch': random.choice(BRANCHES),
            'updated_at': now - random.randint(3600, 10 * 24 * 3600),
            'commit_messages': random.choice(PUSH_COMMITS),
            'score': score,
            'review_result': '## Summary\n- Review Score: 88\n',
            'additions': additions,
            'deletions': deletions,
            'url': f'https://bitbucket.example.com/projects/DEMO/repos/{project}'
                   f'/commits/{random.randint(10**7, 10**8):08x}',
            'status': 'success',
            'error_message': '',
            'platform': 'bitbucket',
            'webhook_data': json.dumps({'eventKey': 'repo:refs_changed'}),
            'author_sole_committer': 1,
        })
        record_id += 1
    return records


def create_schema(conn):
    """建表 —— 结构和生产库一致（含最近加的列）。"""
    conn.executescript("""
        CREATE TABLE mr_review_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            project_name TEXT, author TEXT, source_branch TEXT, target_branch TEXT,
            updated_at INTEGER, commit_messages TEXT, score INTEGER, url TEXT,
            review_result TEXT, additions INTEGER, deletions INTEGER,
            status TEXT DEFAULT 'success', error_message TEXT, platform TEXT,
            webhook_data TEXT, author_sole_committer INTEGER DEFAULT 1,
            pr_id TEXT, pr_repo TEXT);

        CREATE TABLE push_review_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            project_name TEXT, author TEXT, branch TEXT, updated_at INTEGER,
            commit_messages TEXT, score INTEGER, review_result TEXT,
            additions INTEGER, deletions INTEGER, url TEXT,
            status TEXT DEFAULT 'success', error_message TEXT, platform TEXT,
            webhook_data TEXT, author_sole_committer INTEGER DEFAULT 1);
    """)


def insert_records(conn, table, records):
    columns = list(records[0].keys())
    sql = (f"INSERT INTO {table} ({','.join(columns)}) "
           f"VALUES ({','.join('?' * len(columns))})")
    conn.executemany(sql, [[r[c] for c in columns] for r in records])


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--db', default='/tmp/demo-dashboard.db',
                        help='目标 SQLite 文件（会被重建）')
    parser.add_argument('--seed', type=int, default=42,
                        help='随机种子，固定后每次生成的数据一样')
    args = parser.parse_args()

    random.seed(args.seed)
    now = int(time.time())

    # 重建目标库 —— 演示库是一次性的，不该和上次的混在一起
    if os.path.exists(args.db):
        os.remove(args.db)
    os.makedirs(os.path.dirname(args.db) or '.', exist_ok=True)

    conn = sqlite3.connect(args.db)
    create_schema(conn)
    mr_records = build_mr_records(now)
    push_records = build_push_records(now)
    insert_records(conn, 'mr_review_log', mr_records)
    insert_records(conn, 'push_review_log', push_records)
    conn.commit()
    conn.close()

    print(f'演示数据已生成: {args.db}')
    print(f'  MR 记录 {len(mr_records)} 条，Push 记录 {len(push_records)} 条')
    print(f'  项目: {", ".join(PROJECTS)}')
    print(f'  作者: {", ".join(AUTHORS)}')
    print('  不含任何真实信息（项目名/姓名/分支/提交信息/链接均为虚构）')


if __name__ == '__main__':
    main()
