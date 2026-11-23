"""审查评论正文的组装。

评论首行的形态：

- 配了 ``REVIEW_MENTION_USERS``：``Auto Review Result, @junchu1 @alice FYI:``
- 没配：``Auto Review Result:``

各平台的 @ 提醒语法都是正文里的 ``@username``，所以同一份逻辑对
Bitbucket / GitHub / GitLab / Gitea 都适用。
"""

import os
from typing import List


def mention_users() -> List[str]:
    """``REVIEW_MENTION_USERS`` 里配置的用户名。留空表示不提醒任何人。"""
    raw = os.getenv('REVIEW_MENTION_USERS', '')
    users = []
    for item in raw.split(','):
        name = item.strip().lstrip('@').strip()
        if name:
            users.append(name)
    return users


def review_comment_prefix() -> str:
    """评论首行。没有配置提醒用户时不添加任何 @ 提醒。"""
    users = mention_users()
    if not users:
        return 'Auto Review Result:'
    return 'Auto Review Result, ' + ' '.join(f'@{user}' for user in users) + ' FYI:'


def build_review_comment(review_result: str, scope_block: str = '') -> str:
    """完整的审查评论正文：提醒行 + 文件 scope + 模型报告。"""
    parts = [review_comment_prefix()]
    if scope_block:
        parts.append(scope_block)
    parts.append(review_result)
    return '\n'.join(parts)
