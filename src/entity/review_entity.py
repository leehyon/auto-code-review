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
        # 合并所有 commit 的 message 属性，用分号分隔
        return "; ".join(commit["message"].strip() for commit in self.commits)


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
        # 合并所有 commit 的 message 属性，用分号分隔
        return "; ".join(commit["message"].strip() for commit in self.commits)

