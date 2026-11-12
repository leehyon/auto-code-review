import re


# 合并提交的标题特征。各平台的措辞不同，兜底用这些模式判断。
_MERGE_SUBJECT_RE = re.compile(
    r'^merge\s+(branch|pull request|remote-tracking branch|tag|commit|revert)\b',
    re.IGNORECASE)


def _commit_subject(commit) -> str:
    """取一个 commit 的标题（subject）。

    各平台的结构不完全一致：
      - GitLab / Gitea / GitHub 的 webhook 里同时有 title 和 message
      - Bitbucket 我们自己组装的是 title（首行）和 message（完整正文）
    所以优先用 title，没有就取 message 的第一行。
    """
    if not isinstance(commit, dict):
        return str(commit or '').strip()
    title = (commit.get('title') or '').strip()
    if title:
        return title
    return (commit.get('message') or '').strip().split('\n')[0].strip()


def _is_merge_commit(commit) -> bool:
    """判断一个 commit 是不是合并提交。

    合并提交（Merge branch 'dev' into feature/xxx）只是把主干同步进来，
    不是真正的改动，放在提交信息里是噪音。

    优先看父提交数量 —— 合并提交有多个父节点，这是最准的判断；
    没有 parents 字段的平台（GitLab webhook 等）退回看标题。
    """
    if not isinstance(commit, dict):
        return False
    parents = commit.get('parents')
    if isinstance(parents, (list, tuple)) and len(parents) > 1:
        return True
    subject = _commit_subject(commit)
    return bool(_MERGE_SUBJECT_RE.match(subject))


# 只保留前 2 个提交标题，其余用计数表示。
# 表格里本来也只显示前 2 个，全量存下来没有意义，
# 而完整审查报告（review_result）才是真正占空间的那部分。
_MAX_STORED_COMMITS = 2


def _summarize_commits(commits) -> str:
    """把 commit 列表压成一段可入库的文本。

    过滤掉合并提交 -> 只保留前 2 个 subject -> 其余用 "+N" 表示。
    """
    subjects = []
    for commit in commits or []:
        if _is_merge_commit(commit):
            continue
        subject = _commit_subject(commit)
        if subject:
            subjects.append(subject)
    if not subjects:
        return ''
    head = subjects[:_MAX_STORED_COMMITS]
    text = '; '.join(head)
    if len(subjects) > _MAX_STORED_COMMITS:
        text += f' +{len(subjects) - _MAX_STORED_COMMITS}'
    return text


class MergeRequestReviewEntity:
    def __init__(self, project_name: str, author: str, source_branch: str, target_branch: str, updated_at: int,
                 commits: list, score: float, url: str, review_result: str, url_slug: str, webhook_data: dict,
                 additions: int, deletions: int, status: str = 'success', error_message: str = '',
                 platform: str = ''):
        self.project_name = project_name
        self.author = author
        self.source_branch = source_branch
        self.target_branch = target_branch
        self.updated_at = updated_at
        self.commits = commits
        self.score = score
        self.url = url
        self.review_result = review_result
        self.url_slug = url_slug
        self.webhook_data = webhook_data
        self.additions = additions
        self.deletions = deletions
        # success = 审查完成；failed = 审查没跑完（超时/网络错误等）。
        # 失败的记录不出现在 PR 上，但会进 Dashboard 供人工重试。
        self.status = status
        self.error_message = error_message
        # 来源平台，重试时用来分发到对应的处理函数
        self.platform = platform

    @property
    def commit_messages(self):
        """入库用的提交信息：过滤合并提交，只留前 2 个 subject。

        只取 subject 不取完整 message（正文带换行，塞进表格没法看）；
        合并提交是同步主干的噪音，过滤掉。
        """
        return _summarize_commits(self.commits)


class PushReviewEntity:
    def __init__(self, project_name: str, author: str, branch: str, updated_at: int, commits: list, score: float,
                 review_result: str, url_slug: str, webhook_data: dict, additions: int, deletions: int,
                 url: str = '', status: str = 'success', error_message: str = '', platform: str = ''):
        self.project_name = project_name
        self.author = author
        self.branch = branch
        self.updated_at = updated_at
        self.commits = commits
        self.score = score
        self.review_result = review_result
        self.url_slug = url_slug
        self.webhook_data = webhook_data
        self.additions = additions
        self.deletions = deletions
        # 本次推送 head commit 的浏览器链接（Dashboard 的提交链接列用）
        self.url = url
        # success = 审查完成；failed = 审查没跑完（超时/网络错误等）
        self.status = status
        self.error_message = error_message
        # 来源平台，重试时用来分发到对应的处理函数
        self.platform = platform

    @property
    def commit_messages(self):
        """入库用的提交信息：过滤合并提交，只留前 2 个 subject。

        只取 subject 不取完整 message（正文带换行，塞进表格没法看）；
        合并提交是同步主干的噪音，过滤掉。
        """
        return _summarize_commits(self.commits)

