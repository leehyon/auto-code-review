"""回填历史记录的 author_sole_committer 标记。

这个标记表示「PR 的所有 commit 都是提 PR 的人本人提交」。
不是的话，这次审查的代码行数和评分都不算在这个人名下。

历史记录入库时没有这个字段（DEFAULT 1，全部计入统计）。
但它们保存了 PR 的 id、仓库 slug 和项目 key，可以重新向平台查一次
PR 里的 commit 作者，按现在的口径重判一次。

只处理 Bitbucket（当前实际在用的平台）—— 其他平台的历史记录拿不到
PR 的 commit 列表，保持原样（计入统计）。

用法（在容器内跑，能读到 config/.env 的凭据）：
    docker exec auto-code-review-app-1 python scripts/backfill_author_sole_committer.py
    docker exec auto-code-review-app-1 python scripts/backfill_author_sole_committer.py --dry-run
"""

import argparse
import json
import os
import sqlite3
import sys

import requests
import urllib3

sys.path.insert(0, '/app')

from src.entity.review_entity import is_author_sole_committer, normalize_author  # noqa: E402

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

DB_PATH = '/app/data/data.db'


def load_env():
    """从 config/.env 读凭据（不覆盖已有的环境变量）。"""
    env_path = '/app/config/.env'
    if not os.path.exists(env_path):
        return
    with open(env_path, 'r', encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith('#') or '=' not in line:
                continue
            key, value = line.split('=', 1)
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            os.environ.setdefault(key, value)


def pr_coordinates(row):
    """从一条历史记录里取出（项目 key, 仓库 slug, PR id）。

    三个来源任选其一：
      - webhook_data 的 pullRequest（最全）
      - url（形如 .../projects/VGC/repos/<slug>/pull-requests/90）
      - project_name + pullRequest.id
    """
    webhook_data = row['webhook_data']
    pr = {}
    if webhook_data:
        try:
            pr = (json.loads(webhook_data) or {}).get('pullRequest') or {}
        except (ValueError, TypeError):
            pr = {}

    project_key = ''
    repo_slug = ''
    pr_id = pr.get('id')

    from_ref = pr.get('fromRef') or {}
    repo_slug = ((from_ref.get('repository') or {}).get('slug')) or ''

    url = row['url'] or ''
    if '/projects/' in url and '/repos/' in url:
        tail = url.split('/projects/', 1)[1]
        project_key = tail.split('/', 1)[0]
        rest = tail.split('/repos/', 1)[1]
        repo_slug = repo_slug or rest.split('/', 1)[0]
        if not pr_id and '/pull-requests/' in rest:
            pr_id = rest.split('/pull-requests/', 1)[1].split('/')[0].split('?')[0]

    return project_key, repo_slug, pr_id


def fetch_commits(session, base_url, api_path, project_key, repo_slug, pr_id):
    """取 PR 里所有 commit（保留判定需要的字段）。

    翻完所有页（PR 的 commit 可能上百个）。
    title / parents 都要留着 —— 判定「合并提交」要靠它们，
    丢了字段会和线上判定结果不一致。
    """
    commits = []
    start = 0
    limit = 100
    while True:
        resp = session.get(
            f'{base_url}{api_path}/projects/{project_key}/repos/{repo_slug}'
            f'/pull-requests/{pr_id}/commits',
            params={'start': start, 'limit': limit}, timeout=30)
        if resp.status_code != 200:
            raise RuntimeError(f'commits API {resp.status_code}')
        page = resp.json()
        values = page.get('values') or []
        for commit in values:
            message = commit.get('message', '') or ''
            commits.append({
                # 和线上的统一结构保持一致（见各平台的 get_*_commits）：
                # 只有 title / message / author_name，没有 parents。
                # 判定「合并提交」靠的是标题特征，多带字段反而会和线上判定不一致。
                'title': message.split('\n')[0],
                'message': message,
                'author_name': (commit.get('author') or {}).get('name') or '',
            })
        if not values or page.get('isLastPage', True):
            break
        start = page.get('nextPageStart', start + limit)
        if len(commits) > 2000:
            break
    return commits


def pr_branch(row):
    """取 PR 的源分支名（回退查 commit 作者时用）。

    优先用记录里的 source_branch；没有就从 webhook_data 的
    pullRequest.fromRef.displayId 取。
    """
    branch = (row['source_branch'] or '').strip()
    if branch:
        return branch
    webhook_data = row['webhook_data']
    if webhook_data:
        try:
            pr = (json.loads(webhook_data) or {}).get('pullRequest') or {}
        except (ValueError, TypeError):
            return ''
        from_ref = pr.get('fromRef') or {}
        return (from_ref.get('displayId') or '').strip()
    return ''


def fetch_branch_commits(session, base_url, api_path, project_key, repo_slug, branch):
    """PR 已被删除时的回退：按分支名反查 commit 作者。

    PR 删了以后 /pull-requests/{id}/commits 查不到了，
    但分支通常还在，从分支上取 commit 一样能看出作者是谁。

    只取作者，不做「这个 commit 属于这个 PR」的精确归属 ——
    分支上可能有 PR 之外的提交。判定口径是「分支上的提交里有没有别人」，
    只要有别人就不能整块记在提 PR 的人头上。
    """
    resp = session.get(
        f'{base_url}{api_path}/projects/{project_key}/repos/{repo_slug}/commits',
        params={'until': branch, 'limit': 100}, timeout=30)
    if resp.status_code != 200:
        raise RuntimeError(f'branch commits API {resp.status_code}')
    commits = []
    for commit in resp.json().get('values') or []:
        message = commit.get('message', '') or ''
        commits.append({
            'title': message.split('\n')[0],
            'message': message,
            'author_name': (commit.get('author') or {}).get('name') or '',
        })
    if not commits:
        raise RuntimeError('分支上没有 commit')
    return commits


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--dry-run', action='store_true', help='只打印不写库')
    args = parser.parse_args()

    load_env()
    base_url = os.environ['BITBUCKET_URL'].rstrip('/')
    token = os.environ['BITBUCKET_ACCESS_TOKEN']
    api_path = os.environ.get('BITBUCKET_API_PATH', '/rest/api/1.0').rstrip('/')

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        'SELECT id, project_name, author, source_branch, url, webhook_data, author_sole_committer '
        'FROM mr_review_log').fetchall()

    session = requests.Session()
    session.headers.update({'Authorization': 'Bearer ' + token})
    session.verify = False

    changed = 0
    unchanged = 0
    skipped = 0
    for row in rows:
        project_key, repo_slug, pr_id = pr_coordinates(row)
        if not project_key or not repo_slug or not pr_id:
            skipped += 1
            print(f"  id={row['id']}: 缺 PR 坐标，跳过")
            continue
        try:
            commits = fetch_commits(
                session, base_url, api_path, project_key, repo_slug, pr_id)
        except Exception as exc:
            # PR 已被删除（commits 接口 404）—— 退回按分支名反查作者。
            # 分支还在就能看出这些代码是谁写的。
            branch = pr_branch(row)
            if not branch:
                skipped += 1
                print(f"  id={row['id']}: 查 commit 失败（{exc}），也没有分支名，跳过")
                continue
            try:
                commits = fetch_branch_commits(
                    session, base_url, api_path, project_key, repo_slug, branch)
                source = f'分支 {branch}'
            except Exception as branch_exc:
                skipped += 1
                print(f"  id={row['id']}: 查 commit 失败（{exc}），分支回退也失败（{branch_exc}），跳过")
                continue
        else:
            source = f'PR {pr_id}'

        attributable = is_author_sole_committer(row['author'], commits)
        authors = sorted(set(
            normalize_author(c['author_name']) for c in commits if c.get('author_name')))

        old = row['author_sole_committer']
        new = 1 if attributable else 0
        if old == new:
            unchanged += 1
            print(f"  id={row['id']}: [{source}] 作者={authors} 判定={attributable}（不变）")
            continue
        changed += 1
        print(f"  id={row['id']}: [{source}] 作者={authors} 判定={attributable} "
              f"{old} -> {new}")
        if not args.dry_run:
            conn.execute('UPDATE mr_review_log SET author_sole_committer = ? WHERE id = ?',
                         (new, row['id']))

    if not args.dry_run:
        conn.commit()
    conn.close()
    print(f"\n完成：改动 {changed} 条，不变 {unchanged} 条，跳过 {skipped} 条"
          f"{'（dry-run，未写库）' if args.dry_run else ''}")


if __name__ == '__main__':
    main()
