"""PR 链接的解析。

PR 被删除时 Bitbucket 会发 pr:deleted 事件，要找到对应的审查记录。
定位靠入库时存的 pr_id / pr_repo，但老记录没有这两列 —— 只能从
它们的 url 里反解出来补上。

各平台的 PR 链接长得不一样：
  Bitbucket  .../projects/<KEY>/repos/<SLUG>/pull-requests/<ID>[/overview]
  GitHub     .../<OWNER>/<REPO>/pull/<ID>
  GitLab     .../<OWNER>/<REPO>/-/merge_requests/<ID>
  Gitea      .../<OWNER>/<REPO>/pulls/<ID>
这里只处理 Bitbucket（当前实际在用的平台），其余平台的链接
本来也解析不出仓库 slug，交给调用方按空值处理。
"""

import re

# .../projects/<KEY>/repos/<SLUG>/pull-requests/<ID>
BITBUCKET_PR_URL_RE = re.compile(
    r'/projects/[^/]+/repos/([^/]+)/pull-requests/(\d+)', re.IGNORECASE)


def parse_bitbucket_pr_ref(url):
    """从 Bitbucket 的 PR 链接里解析出（仓库 slug, PR id）。

    解析不出来返回 (None, None) —— 调用方要自己判断，
    不能把 None 当成「匹配所有」。
    """
    if not url or not isinstance(url, str):
        return None, None
    match = BITBUCKET_PR_URL_RE.search(url)
    if not match:
        return None, None
    return match.group(1), match.group(2)
