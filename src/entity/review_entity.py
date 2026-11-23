import re


def normalize_author(author):
    """作者名归整：只保留第一个点之前的部分。

    Bitbucket 的用户名是「名.姓」形式（yingchao.han），别处是短名（yingchao）。
    两种写法指的是同一个人，不归整的话统计里会裂成两项、筛选框里也是两条。

    语义必须和 SQL 里的归整表达式完全一致（见 review_service._AUTHOR_NORMALIZED_SQL），
    否则显示出来的名字和筛选框选不中的名字会不是一回事。

    非字符串（None / NaN）原样返回，不归成空串 —— 空串会在筛选框里变成一个空选项。
    """
    if not isinstance(author, str):
        return author
    if '.' in author:
        head = author.split('.', 1)[0]
        # 开头就是点的（'.foo'）不要归成空串
        if head:
            return head
    return author


def _commit_author(commit):
    """取一个 commit 的作者名。

    各平台的统一结构里作者字段名不一样：
      - Bitbucket / GitHub / Gitea 用 author_name
      - GitLab / Gitea 用 author
    取不到就返回空串（调用方按「拿不到作者」处理）。
    """
    if not isinstance(commit, dict):
        return ''
    for key in ('author_name', 'author'):
        value = commit.get(key)
        if isinstance(value, str) and value:
            return value
    return ''


def commit_authors(commits) -> set:
    """取一组 commit 的作者集合（已归整）。

    合并提交也计入。Bitbucket 的 PR 合并提交（标题是「Pull request #N: ...」）
    的 author 是**代码的捐献者**，不是执行合并的那个人 ——
    排除它就等于把别人的代码记到提 PR 的人头上。
    """
    authors = set()
    for commit in commits or []:
        name = normalize_author(_commit_author(commit))
        if name:
            authors.add(name)
    return authors


def is_author_sole_committer(pr_author, commits) -> bool:
    """判断 PR 的所有 commit 是不是都由提 PR 的人本人提交。

    口径：**所有** commit 的作者都是提 PR 的人本人，才算一致。
    只要有任何一个 commit 来自别人，这次的 +N -M 和评分就不知道该算到谁头上
    （Bitbucket 没有 per-commit 的行数接口，算不出精确分摊），
    整块不算在提 PR 的人名下，比错记在一个人头上更诚实。

    拿不到 commit 作者时按「无法证明是本人」处理 —— 不算。
    """
    authors = commit_authors(commits)
    if not authors:
        return False
    return authors == {normalize_author(pr_author)}


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
                 platform: str = '', pr_id: str = '', pr_repo: str = ''):
        # 项目名统一小写：各平台给的名称大小写不一致（Bitbucket 的 display name
        # 是大写，slug 是小写；GitLab 用的是人类可读名），不统一的话同一个项目
        # 会在统计和筛选里裂成好几项。
        self.project_name = (project_name or '').lower()
        # 作者名同样归整：Bitbucket 的用户名是「名.姓」（yingchao.han），
        # 别的平台是短名（yingchao），不归整同一个人会裂成两项。
        self.author = normalize_author(author)
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
        # PR 的所有 commit 是否都由提 PR 的人本人提交。
        # 不是的话，这次的 +N -M 和评分都不算在这个人的名下 ——
        # PR 里混了别人的 commit，这份代码不是他写的。
        self.author_sole_committer = is_author_sole_committer(author, commits)
        # success = 审查完成；failed = 审查没跑完（超时/网络错误等）。
        # 失败的记录不出现在 PR 上，但会进 Dashboard 供人工重试。
        self.status = status
        self.error_message = error_message
        # 来源平台，重试时用来分发到对应的处理函数
        self.platform = platform
        # PR 的 id 和仓库 slug。
        # PR 被删除时 Bitbucket 会发 pr:deleted 事件，靠这两个字段
        # 找到对应的记录删掉 —— 靠 URL 字符串匹配太脆弱。
        self.pr_id = str(pr_id or '')
        self.pr_repo = pr_repo or ''

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
        # 同样统一小写，理由同 MergeRequestReviewEntity
        self.project_name = (project_name or '').lower()
        # 作者名同样归整，理由同 MergeRequestReviewEntity
        self.author = normalize_author(author)
        self.branch = branch
        self.updated_at = updated_at
        self.commits = commits
        self.score = score
        self.review_result = review_result
        self.url_slug = url_slug
        self.webhook_data = webhook_data
        self.additions = additions
        self.deletions = deletions
        # 同 MergeRequestReviewEntity：推送里混了别人的 commit 时，
        # 这 +N -M 和评分都不计入任何人的名下。
        self.author_sole_committer = is_author_sole_committer(author, commits)
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

