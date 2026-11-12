import os
import traceback
from datetime import datetime

from src.entity.review_entity import MergeRequestReviewEntity, PushReviewEntity
from src.event.event_manager import event_manager
from src.gitlab.webhook_handler import filter_changes, MergeRequestHandler, PushHandler
from src.github.webhook_handler import filter_changes as filter_github_changes, PullRequestHandler as GithubPullRequestHandler, PushHandler as GithubPushHandler
from src.gitea.webhook_handler import filter_changes as filter_gitea_changes, PullRequestHandler as GiteaPullRequestHandler, PushHandler as GiteaPushHandler
from src.bitbucket.webhook_handler import filter_changes as filter_bitbucket_changes, PullRequestHandler as BitbucketPullRequestHandler, PushHandler as BitbucketPushHandler
from src.service.review_service import ReviewService
from src.utils.code_reviewer import CodeReviewer
from src.utils.messaging import notifier
from src.utils.log import logger


def _record_failed_review(review_type: str, context: dict, error_message: str):
    """把失败的审查写进数据库。

    失败时**不在 PR 上留评论**（免得刷屏，也让用户一眼看出评论都是真结果），
    但要出现在 Dashboard 里——否则失败就是完全静默的，人只会觉得"这工具没反应"。

    记录里带上触发时的 webhook payload，重试时用它还原现场。
    """
    try:
        if review_type == 'push':
            entity = PushReviewEntity(
                project_name=context.get('project_name', ''),
                author=context.get('author', ''),
                branch=context.get('branch', ''),
                updated_at=int(datetime.now().timestamp()),
                commits=context.get('commits') or [],
                score=0,
                review_result='',
                url_slug=context.get('url_slug', ''),
                webhook_data=context.get('webhook_data') or {},
                additions=context.get('additions', 0),
                deletions=context.get('deletions', 0),
                url=context.get('url', ''),
                status='failed',
                error_message=error_message,
                platform=context.get('platform', ''))
            ReviewService.insert_push_review_log(entity)
        else:
            entity = MergeRequestReviewEntity(
                project_name=context.get('project_name', ''),
                author=context.get('author', ''),
                source_branch=context.get('source_branch', ''),
                target_branch=context.get('target_branch', ''),
                updated_at=int(datetime.now().timestamp()),
                commits=context.get('commits') or [],
                score=0,
                url=context.get('url', ''),
                review_result='',
                url_slug=context.get('url_slug', ''),
                webhook_data=context.get('webhook_data') or {},
                additions=context.get('additions', 0),
                deletions=context.get('deletions', 0),
                status='failed',
                error_message=error_message,
                platform=context.get('platform', ''))
            ReviewService.insert_mr_review_log(entity)
    except Exception:
        logger.error('Failed to record the failed review', exc_info=True)


def _retry_handlers():
    """(记录类型, 平台) -> 重跑函数。

    重试靠重放触发时的 webhook payload，所以各平台入口签名一致就行。
    """
    return {
        ('mr', 'bitbucket'): lambda payload: handle_bitbucket_pull_request_event(
            payload,
            os.environ.get('BITBUCKET_ACCESS_TOKEN', ''),
            os.environ.get('BITBUCKET_URL', ''),
            'bitbucket'),
        ('push', 'bitbucket'): lambda payload: handle_bitbucket_push_event(
            payload,
            os.environ.get('BITBUCKET_ACCESS_TOKEN', ''),
            os.environ.get('BITBUCKET_URL', ''),
            'bitbucket'),
        ('mr', 'github'): lambda payload: handle_github_pull_request_event(
            payload,
            os.environ.get('GITHUB_ACCESS_TOKEN', ''),
            os.environ.get('GITHUB_URL', 'https://github.com'),
            'github'),
        ('push', 'github'): lambda payload: handle_github_push_event(
            payload,
            os.environ.get('GITHUB_ACCESS_TOKEN', ''),
            os.environ.get('GITHUB_URL', 'https://github.com'),
            'github'),
        ('mr', 'gitlab'): lambda payload: handle_merge_request_event(
            payload,
            os.environ.get('GITLAB_ACCESS_TOKEN', ''),
            os.environ.get('GITLAB_URL', ''),
            'gitlab'),
        ('push', 'gitlab'): lambda payload: handle_push_event(
            payload,
            os.environ.get('GITLAB_ACCESS_TOKEN', ''),
            os.environ.get('GITLAB_URL', ''),
            'gitlab'),
        ('mr', 'gitea'): lambda payload: handle_gitea_pull_request_event(
            payload,
            os.environ.get('GITEA_ACCESS_TOKEN', ''),
            os.environ.get('GITEA_URL', ''),
            'gitea'),
        ('push', 'gitea'): lambda payload: handle_gitea_push_event(
            payload,
            os.environ.get('GITEA_ACCESS_TOKEN', ''),
            os.environ.get('GITEA_URL', ''),
            'gitea'),
    }


def retry_failed_review(review_type: str, record_id: int) -> dict:
    """重跑一次失败的审查。

    流程：读出失败记录 -> 删掉它 -> 用保存的 webhook payload 原样重放。
    重放走正常审查流程：成功就发评论并写入新记录，还是失败就再写一条 failed 记录。
    """
    record = ReviewService.get_review_log(review_type, record_id)
    if not record:
        return {'success': False, 'message': '记录不存在，可能已经被重试过'}

    payload = record.get('webhook_data') or {}
    if not payload:
        return {'success': False, 'message': '这条记录没有保存触发时的请求内容，无法重试'}

    platform = (record.get('platform') or '').lower()
    handler = _retry_handlers().get((review_type, platform))
    if handler is None:
        return {'success': False,
                'message': f'暂不支持 {platform or "未知平台"} 的重试'}

    logger.info(f"Retrying failed review: type={review_type} id={record_id} platform={platform}")

    # 不要先删失败行：重放可能走「没有变更」这种正常返回路径（不抛异常），
    # 那样既没写出新记录，旧记录又被删了 —— 失败记录会凭空消失。
    # 先重放，再根据有没有产出新记录决定旧记录怎么处理。
    try:
        handler(payload)
    except Exception as e:
        logger.error(f"Retry raised an exception: {e}", exc_info=True)

    # 新写入的记录 id 一定比旧的大
    newest = ReviewService.get_newest_review_log_id(review_type)
    if newest and newest > record_id:
        # 新记录顶上，删掉旧的失败行，Dashboard 上不留重复
        ReviewService.delete_review_log(review_type, record_id)
        return {'success': True, 'message': '已重新提交审查'}

    # 没有任何产出：保留失败行，并把「重试也没结果」写进错误信息，
    # 不能让它凭空消失 —— 用户需要知道这条还是失败的。
    reason = (record.get('error_message') or '').strip()
    merged = f"{reason} | 重试后仍未产生审查结果" if reason else '重试后仍未产生审查结果'
    ReviewService.update_review_log(review_type, record_id, 'failed', merged)
    return {'success': False, 'message': '重试后仍未产生审查结果，请查看错误信息'}


def _safe_get(container, *keys, default=''):
    """安全地从嵌套 dict 取值，任何一层缺失都返回默认值。"""
    current = container
    for key in keys:
        if not isinstance(current, dict):
            return default
        current = current.get(key)
        if current is None:
            return default
    return current


def handle_push_event(webhook_data: dict, gitlab_token: str, gitlab_url: str, gitlab_url_slug: str):
    push_review_enabled = os.environ.get('PUSH_REVIEW_ENABLED', '0') == '1'
    handler = None
    commits = []
    additions = 0
    deletions = 0
    try:
        handler = PushHandler(webhook_data, gitlab_token, gitlab_url)
        logger.info('Push Hook event received')
        commits = handler.get_push_commits()
        if not commits:
            logger.error('Failed to get commits')
            return

        review_result = None
        score = 0
        additions = 0
        deletions = 0
        if push_review_enabled:
            # 获取PUSH的changes
            changes = handler.get_push_changes()
            logger.info('changes: %s', changes)
            changes = filter_changes(changes)
            if not changes:
                logger.info('未检测到PUSH代码的修改,修改文件可能不满足SUPPORTED_EXTENSIONS。')
            review_result = "关注的文件没有修改"
            reviewer = CodeReviewer()

            if len(changes) > 0:
                commits_text = ';'.join(commit.get('message', '').strip() for commit in commits)
                review_result = reviewer.review_and_strip_code(changes, commits_text, changes)
                score = CodeReviewer.parse_review_score(review_text=review_result)
                for item in changes:
                    additions += item['additions']
                    deletions += item['deletions']
            # 将review结果提交到Gitlab的 notes
            handler.add_push_notes(reviewer.format_review_comment(review_result))

        event_manager['push_reviewed'].send(PushReviewEntity(
            project_name=webhook_data['project']['name'],
            author=webhook_data['user_username'],
            branch=webhook_data['project']['default_branch'],
            updated_at=int(datetime.now().timestamp()),  # 当前时间
            commits=commits,
            score=score,
            review_result=review_result,
            url_slug=gitlab_url_slug,
            webhook_data=webhook_data,
            additions=additions,
            deletions=deletions,
            url=handler.push_url,
        ))

    except Exception as e:
        error_message = f'服务出现未知错误: {str(e)}\n{traceback.format_exc()}'
        _record_failed_review('push', {
            'project_name': _safe_get(webhook_data, 'project', 'name'),
            'author': _safe_get(webhook_data, 'user_username'),
            'branch': _safe_get(webhook_data, 'project', 'default_branch'),
            'commits': commits,
            'additions': additions,
            'deletions': deletions,
            'url': getattr(handler, 'push_url', '') if handler else '',
            'webhook_data': webhook_data,
            'url_slug': gitlab_url_slug,
            'platform': 'gitlab',
        }, error_message)
        notifier.send_notification(content=error_message)
        logger.error('出现未知错误: %s', error_message)


def handle_merge_request_event(webhook_data: dict, gitlab_token: str, gitlab_url: str, gitlab_url_slug: str):
    '''
    处理Merge Request Hook事件
    :param webhook_data:
    :param gitlab_token:
    :param gitlab_url:
    :param gitlab_url_slug:
    :return:
    '''
    merge_review_only_protected_branches = os.environ.get('MERGE_REVIEW_ONLY_PROTECTED_BRANCHES_ENABLED', '0') == '1'
    merge_review_only_branch_name_str = os.environ.get('MERGE_REVIEW_ONLY_BRANCH_NAME', '')  # 需要review的分支名称，逗号分隔
    merge_review_only_branch_names = [b.strip() for b in merge_review_only_branch_name_str.split(',') if b.strip()] if merge_review_only_branch_name_str else []


    commits = []
    additions = 0
    deletions = 0
    try:
        # 解析Webhook数据
        handler = MergeRequestHandler(webhook_data, gitlab_token, gitlab_url)
        logger.info('Merge Request Hook event received')
        # 如果开启了仅review projected branches的，判断当前目标分支是否为projected branches
        if merge_review_only_protected_branches and not handler.target_branch_protected():
            logger.info("Merge Request target branch not match protected branches, ignored.")
            return
        # 判断分支名称进行过滤
        if merge_review_only_branch_names and webhook_data['object_attributes']['target_branch'] not in merge_review_only_branch_names:
            logger.info(f"Merge Request Hook event, branch={webhook_data['object_attributes']['target_branch']}, ignored.")
            return
        if handler.action not in ['open', 'update']:
            logger.info(f"Merge Request Hook event, action={handler.action}, ignored.")
            return

        # 仅仅在MR创建或更新时进行Code Review
        # 获取Merge Request的changes
        changes = handler.get_merge_request_changes()
        logger.info('changes: %s', changes)
        changes = filter_changes(changes)
        if not changes:
            logger.info('未检测到有关代码的修改,修改文件可能不满足SUPPORTED_EXTENSIONS。')
            return
        # 统计本次新增、删除的代码总数
        additions = 0
        deletions = 0
        for item in changes:
            additions += item.get('additions', 0)
            deletions += item.get('deletions', 0)

        # 获取Merge Request的commits
        commits = handler.get_merge_request_commits()
        if not commits:
            logger.error('Failed to get commits')
            return

        # review 代码
        commits_text = ';'.join(commit['title'] for commit in commits)
        reviewer = CodeReviewer()
        review_result = reviewer.review_and_strip_code(changes, commits_text, changes)

        # 将review结果提交到Gitlab的 notes
        handler.add_merge_request_notes(reviewer.format_review_comment(review_result))

        # dispatch merge_request_reviewed event
        event_manager['merge_request_reviewed'].send(
            MergeRequestReviewEntity(
                project_name=webhook_data['project']['name'],
                author=webhook_data['user']['username'],
                source_branch=webhook_data['object_attributes']['source_branch'],
                target_branch=webhook_data['object_attributes']['target_branch'],
                updated_at=int(datetime.now().timestamp()),
                commits=commits,
                score=CodeReviewer.parse_review_score(review_text=review_result),
                url=webhook_data['object_attributes']['url'],
                review_result=review_result,
                url_slug=gitlab_url_slug,
                webhook_data=webhook_data,
                additions=additions,
                deletions=deletions,
            )
        )

    except Exception as e:
        error_message = f'AI Code Review 服务出现未知错误: {str(e)}\n{traceback.format_exc()}'
        _record_failed_review('mr', {
            'project_name': _safe_get(webhook_data, 'project', 'name'),
            'author': _safe_get(webhook_data, 'user', 'username'),
            'source_branch': _safe_get(webhook_data, 'object_attributes', 'source_branch'),
            'target_branch': _safe_get(webhook_data, 'object_attributes', 'target_branch'),
            'commits': commits,
            'additions': additions,
            'deletions': deletions,
            'url': _safe_get(webhook_data, 'object_attributes', 'url'),
            'webhook_data': webhook_data,
            'url_slug': gitlab_url_slug,
            'platform': 'gitlab',
        }, error_message)
        notifier.send_notification(content=error_message)
        logger.error('出现未知错误: %s', error_message)

def handle_github_push_event(webhook_data: dict, github_token: str, github_url: str, github_url_slug: str):
    push_review_enabled = os.environ.get('PUSH_REVIEW_ENABLED', '0') == '1'
    handler = None
    commits = []
    additions = 0
    deletions = 0
    try:
        handler = GithubPushHandler(webhook_data, github_token, github_url)
        logger.info('GitHub Push event received')
        commits = handler.get_push_commits()
        if not commits:
            logger.error('Failed to get commits')
            return

        review_result = None
        score = 0
        additions = 0
        deletions = 0
        if push_review_enabled:
            # 获取PUSH的changes
            changes = handler.get_push_changes()
            logger.info('changes: %s', changes)
            changes = filter_github_changes(changes)
            if not changes:
                logger.info('未检测到PUSH代码的修改,修改文件可能不满足SUPPORTED_EXTENSIONS。')
            review_result = "关注的文件没有修改"
            reviewer = CodeReviewer()

            if len(changes) > 0:
                commits_text = ';'.join(commit.get('message', '').strip() for commit in commits)
                review_result = reviewer.review_and_strip_code(changes, commits_text, changes)
                score = CodeReviewer.parse_review_score(review_text=review_result)
                for item in changes:
                    additions += item.get('additions', 0)
                    deletions += item.get('deletions', 0)
            # 将review结果提交到GitHub的 notes
            handler.add_push_notes(reviewer.format_review_comment(review_result))

        event_manager['push_reviewed'].send(PushReviewEntity(
            project_name=webhook_data['repository']['name'],
            author=webhook_data['sender']['login'],
            branch=webhook_data['ref'].replace('refs/heads/', ''),
            updated_at=int(datetime.now().timestamp()),  # 当前时间
            commits=commits,
            score=score,
            review_result=review_result,
            url_slug=github_url_slug,
            webhook_data=webhook_data,
            additions=additions,
            deletions=deletions,
            url=handler.push_url,
        ))

    except Exception as e:
        error_message = f'服务出现未知错误: {str(e)}\n{traceback.format_exc()}'
        _record_failed_review('push', {
            'project_name': _safe_get(webhook_data, 'repository', 'name'),
            'author': _safe_get(webhook_data, 'sender', 'login'),
            'branch': _safe_get(webhook_data, 'ref').replace('refs/heads/', ''),
            'commits': commits,
            'additions': additions,
            'deletions': deletions,
            'url': getattr(handler, 'push_url', '') if handler else '',
            'webhook_data': webhook_data,
            'url_slug': github_url_slug,
            'platform': 'github',
        }, error_message)
        notifier.send_notification(content=error_message)
        logger.error('出现未知错误: %s', error_message)


def handle_github_pull_request_event(webhook_data: dict, github_token: str, github_url: str, github_url_slug: str):
    '''
    处理GitHub Pull Request 事件
    :param webhook_data:
    :param github_token:
    :param github_url:
    :param github_url_slug:
    :return:
    '''
    merge_review_only_protected_branches = os.environ.get('MERGE_REVIEW_ONLY_PROTECTED_BRANCHES_ENABLED', '0') == '1'
    commits = []
    additions = 0
    deletions = 0
    try:
        # 解析Webhook数据
        handler = GithubPullRequestHandler(webhook_data, github_token, github_url)
        logger.info('GitHub Pull Request event received')
        # 如果开启了仅review projected branches的，判断当前目标分支是否为projected branches
        if merge_review_only_protected_branches and not handler.target_branch_protected():
            logger.info("Merge Request target branch not match protected branches, ignored.")
            return

        if handler.action not in ['opened', 'synchronize']:
            logger.info(f"Pull Request Hook event, action={handler.action}, ignored.")
            return

        # 仅仅在PR创建或更新时进行Code Review
        # 获取Pull Request的changes
        changes = handler.get_pull_request_changes()
        logger.info('changes: %s', changes)
        changes = filter_github_changes(changes)
        if not changes:
            logger.info('未检测到有关代码的修改,修改文件可能不满足SUPPORTED_EXTENSIONS。')
            return
        # 统计本次新增、删除的代码总数
        additions = 0
        deletions = 0
        for item in changes:
            additions += item.get('additions', 0)
            deletions += item.get('deletions', 0)

        # 获取Pull Request的commits
        commits = handler.get_pull_request_commits()
        if not commits:
            logger.error('Failed to get commits')
            return

        # review 代码
        commits_text = ';'.join(commit['title'] for commit in commits)
        reviewer = CodeReviewer()
        review_result = reviewer.review_and_strip_code(changes, commits_text, changes)

        # 将review结果提交到GitHub的 notes
        handler.add_pull_request_notes(reviewer.format_review_comment(review_result))

        # dispatch pull_request_reviewed event
        event_manager['merge_request_reviewed'].send(
            MergeRequestReviewEntity(
                project_name=webhook_data['repository']['name'],
                author=webhook_data['pull_request']['user']['login'],
                source_branch=webhook_data['pull_request']['head']['ref'],
                target_branch=webhook_data['pull_request']['base']['ref'],
                updated_at=int(datetime.now().timestamp()),
                commits=commits,
                score=CodeReviewer.parse_review_score(review_text=review_result),
                url=webhook_data['pull_request']['html_url'],
                review_result=review_result,
                url_slug=github_url_slug,
                webhook_data=webhook_data,
                additions=additions,
                deletions=deletions,
            ))

    except Exception as e:
        error_message = f'服务出现未知错误: {str(e)}\n{traceback.format_exc()}'
        _record_failed_review('mr', {
            'project_name': _safe_get(webhook_data, 'repository', 'name'),
            'author': _safe_get(webhook_data, 'pull_request', 'user', 'login'),
            'source_branch': _safe_get(webhook_data, 'pull_request', 'head', 'ref'),
            'target_branch': _safe_get(webhook_data, 'pull_request', 'base', 'ref'),
            'commits': commits,
            'additions': additions,
            'deletions': deletions,
            'url': _safe_get(webhook_data, 'pull_request', 'html_url'),
            'webhook_data': webhook_data,
            'url_slug': github_url_slug,
            'platform': 'github',
        }, error_message)
        notifier.send_notification(content=error_message)
        logger.error('出现未知错误: %s', error_message)


def handle_gitea_push_event(webhook_data: dict, gitea_token: str, gitea_url: str, gitea_url_slug: str):
    push_review_enabled = os.environ.get('PUSH_REVIEW_ENABLED', '0') == '1'
    handler = None
    commits = []
    additions = 0
    deletions = 0
    project_name = ''
    author = ''
    branch = ''
    try:
        handler = GiteaPushHandler(webhook_data, gitea_token, gitea_url)
        logger.info('Gitea Push event received')
        commits = handler.get_push_commits()
        if not commits:
            logger.error('Failed to get commits')
            return

        # 提取项目名称和作者信息（提前提取，供后续使用）
        repository = webhook_data.get('repository', {})
        project_name = repository.get('name', '')
        pusher = webhook_data.get('pusher', {})
        author = pusher.get('username', '') if isinstance(pusher, dict) else ''
        if not author:
            # 尝试从 commits 中获取作者
            if commits:
                author = commits[0].get('author', '')
        
        branch = webhook_data.get('ref', '').replace('refs/heads/', '')

        review_result = None
        score = 0
        additions = 0
        deletions = 0
        if push_review_enabled:
            # 获取PUSH的changes
            changes = handler.get_push_changes()
            logger.info('changes: %s', changes)
            changes = filter_gitea_changes(changes)
            if not changes:
                logger.info('未检测到PUSH代码的修改,修改文件可能不满足SUPPORTED_EXTENSIONS。')
            review_result = "关注的文件没有修改"
            reviewer = CodeReviewer()

            if len(changes) > 0:
                commits_text = ';'.join(commit.get('message', '').strip() for commit in commits)
                review_result = reviewer.review_and_strip_code(changes, commits_text, changes)
                score = CodeReviewer.parse_review_score(review_text=review_result)
                for item in changes:
                    additions += item.get('additions', 0)
                    deletions += item.get('deletions', 0)
            
            # 检查是否启用 Issue 模式（默认开启）
            use_issue_mode = os.environ.get('GITEA_USE_ISSUE_MODE', '1') == '1'
            
            if use_issue_mode and len(changes) > 0:
                # Issue 模式：创建/获取 Issue 并添加评论
                try:
                    # 准备审核元数据
                    last_commit = commits[-1] if commits else {}
                    commit_sha = last_commit.get('url', '').split('/')[-1] if last_commit.get('url') else ''
                    if not commit_sha and handler.commit_list:
                        commit_sha = handler.commit_list[-1].get('id', '')
                    
                    review_metadata = {
                        'author': author or 'unknown',
                        'commit_sha': commit_sha,
                        'review_time': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                        'file_count': len(changes),
                        'additions': additions,
                        'deletions': deletions,
                        'files': [change.get('new_path', '') for change in changes]
                    }
                    
                    issue_number = handler.create_or_get_review_issue(review_metadata)
                    handler.add_issue_comment(issue_number, reviewer.format_review_comment(review_result))
                    logger.info(f"Review result added to issue #{issue_number}")
                except Exception as e:
                    logger.error(f"Failed to add review to issue: {str(e)}")
                    import traceback
                    logger.debug(traceback.format_exc())
                    # 降级到传统模式
                    handler.add_push_notes(reviewer.format_review_comment(review_result))
            else:
                # 传统模式：直接在 commit 上添加评论
                handler.add_push_notes(reviewer.format_review_comment(review_result))

        event_manager['push_reviewed'].send(PushReviewEntity(
            project_name=project_name,
            author=author,
            branch=branch,
            updated_at=int(datetime.now().timestamp()),  # 当前时间
            commits=commits,
            score=score,
            review_result=review_result,
            url_slug=gitea_url_slug,
            webhook_data=webhook_data,
            additions=additions,
            deletions=deletions,
            url=handler.push_url,
        ))

    except Exception as e:
        error_message = f'服务出现未知错误: {str(e)}\n{traceback.format_exc()}'
        _record_failed_review('push', {
            'project_name': project_name,
            'author': author,
            'branch': branch,
            'commits': commits,
            'additions': additions,
            'deletions': deletions,
            'url': getattr(handler, 'push_url', '') if handler else '',
            'webhook_data': webhook_data,
            'url_slug': gitea_url_slug,
            'platform': 'gitea',
        }, error_message)
        notifier.send_notification(content=error_message)
        logger.error('出现未知错误: %s', error_message)


def handle_gitea_pull_request_event(webhook_data: dict, gitea_token: str, gitea_url: str, gitea_url_slug: str):
    '''
    处理Gitea Pull Request 事件
    :param webhook_data:
    :param gitea_token:
    :param gitea_url:
    :param gitea_url_slug:
    :return:
    '''
    merge_review_only_protected_branches = os.environ.get('MERGE_REVIEW_ONLY_PROTECTED_BRANCHES_ENABLED', '0') == '1'
    commits = []
    additions = 0
    deletions = 0
    project_name = ''
    author = ''
    source_branch = ''
    target_branch = ''
    html_url = ''
    try:
        # 解析Webhook数据
        handler = GiteaPullRequestHandler(webhook_data, gitea_token, gitea_url)
        logger.info('Gitea Pull Request event received')
        
        # 提取项目名称和作者信息（提前提取，供后续使用）
        pull_request = webhook_data.get('pull_request', {})
        repository = webhook_data.get('repository', {})
        project_name = repository.get('name', '')
        sender = webhook_data.get('sender', {})
        author = sender.get('login', '') if isinstance(sender, dict) else ''
        if not author:
            # 尝试从 pull_request.user 获取
            pr_user = pull_request.get('user', {})
            author = pr_user.get('login', '') if isinstance(pr_user, dict) else ''
        
        # 如果开启了仅review protected branches的，判断当前目标分支是否为protected branches
        if merge_review_only_protected_branches and not handler.target_branch_protected():
            logger.info("Pull Request target branch not match protected branches, ignored.")
            return

        if handler.action not in ['opened', 'synchronize']:
            logger.info(f"Pull Request Hook event, action={handler.action}, ignored.")
            return

        # 仅仅在PR创建或更新时进行Code Review
        # 获取Pull Request的changes
        changes = handler.get_pull_request_changes()
        logger.info('changes: %s', changes)
        changes = filter_gitea_changes(changes)
        if not changes:
            logger.info('未检测到有关代码的修改,修改文件可能不满足SUPPORTED_EXTENSIONS。')
            return
        # 统计本次新增、删除的代码总数
        additions = 0
        deletions = 0
        for item in changes:
            additions += item.get('additions', 0)
            deletions += item.get('deletions', 0)

        # 获取Pull Request的commits
        commits = handler.get_pull_request_commits()
        if not commits:
            logger.error('Failed to get commits')
            return

        # review 代码
        commits_text = ';'.join(commit.get('title', commit.get('message', '')).split('\n')[0] for commit in commits)
        reviewer = CodeReviewer()
        review_result = reviewer.review_and_strip_code(changes, commits_text, changes)

        # 检查是否启用 Issue 模式（默认开启）
        use_issue_mode = os.environ.get('GITEA_USE_ISSUE_MODE', '1') == '1'
        
        if use_issue_mode:
            # Issue 模式：创建/获取 Issue 并添加评论
            try:
                # 准备审核元数据
                review_metadata = {
                    'author': author,
                    'review_time': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                    'file_count': len(changes),
                    'additions': additions,
                    'deletions': deletions,
                    'files': [change.get('new_path', '') for change in changes]
                }
                
                issue_number = handler.create_or_get_review_issue(review_metadata)
                handler.add_issue_comment(issue_number, reviewer.format_review_comment(review_result))
                logger.info(f"Review result added to issue #{issue_number}")
            except Exception as e:
                logger.error(f"Failed to add review to issue: {str(e)}")
                import traceback
                logger.debug(traceback.format_exc())
                # 降级到 PR 评论模式
                handler.add_pull_request_notes(reviewer.format_review_comment(review_result))
        else:
            # 传统模式：直接在 PR 上添加评论
            handler.add_pull_request_notes(reviewer.format_review_comment(review_result))

        # 提取项目信息
        pull_request = webhook_data.get('pull_request', {})
        repository = webhook_data.get('repository', {})
        project_name = repository.get('name', '')
        sender = webhook_data.get('sender', {})
        author = sender.get('login', '') if isinstance(sender, dict) else ''
        
        base = pull_request.get('base', {})
        head = pull_request.get('head', {})
        source_branch = head.get('ref', '')
        target_branch = base.get('ref', '')
        
        # 获取 PR URL
        html_url = pull_request.get('html_url', '')
        if not html_url:
            # 构建 URL
            repo_full_name = handler.repo_full_name
            pr_number = handler.pull_request_number
            html_url = f"{gitea_url}/{repo_full_name}/pulls/{pr_number}"

        # dispatch pull_request_reviewed event
        event_manager['merge_request_reviewed'].send(
            MergeRequestReviewEntity(
                project_name=project_name,
                author=author,
                source_branch=source_branch,
                target_branch=target_branch,
                updated_at=int(datetime.now().timestamp()),
                commits=commits,
                score=CodeReviewer.parse_review_score(review_text=review_result),
                url=html_url,
                review_result=review_result,
                url_slug=gitea_url_slug,
                webhook_data=webhook_data,
                additions=additions,
                deletions=deletions,
            ))

    except Exception as e:
        error_message = f'服务出现未知错误: {str(e)}\n{traceback.format_exc()}'
        _record_failed_review('mr', {
            'project_name': project_name,
            'author': author,
            'source_branch': source_branch,
            'target_branch': target_branch,
            'commits': commits,
            'additions': additions,
            'deletions': deletions,
            'url': html_url,
            'webhook_data': webhook_data,
            'url_slug': gitea_url_slug,
            'platform': 'gitea',
        }, error_message)
        notifier.send_notification(content=error_message)
        logger.error('出现未知错误: %s', error_message)


def handle_bitbucket_pull_request_event(webhook_data: dict, bitbucket_token: str, bitbucket_url: str,
                                        bitbucket_url_slug: str):
    '''
    处理Bitbucket Server/Data Center Pull Request 事件
    :param webhook_data:
    :param bitbucket_token:
    :param bitbucket_url:
    :param bitbucket_url_slug:
    :return:
    '''
    merge_review_only_protected_branches = os.environ.get('MERGE_REVIEW_ONLY_PROTECTED_BRANCHES_ENABLED', '0') == '1'
    merge_review_only_branch_name_str = os.environ.get('MERGE_REVIEW_ONLY_BRANCH_NAME', '')  # 需要review的分支名称，逗号分隔
    merge_review_only_branch_names = [b.strip() for b in merge_review_only_branch_name_str.split(',') if b.strip()] if merge_review_only_branch_name_str else []

    handler = None
    commits = []
    additions = 0
    deletions = 0
    try:
        # 解析Webhook数据
        handler = BitbucketPullRequestHandler(webhook_data, bitbucket_token, bitbucket_url)
        logger.info('Bitbucket Pull Request event received')

        # 如果开启了仅review protected branches的，判断当前目标分支是否为protected branches
        if merge_review_only_protected_branches and not handler.target_branch_protected():
            logger.info("Pull Request target branch not match protected branches, ignored.")
            return

        # 判断分支名称进行过滤
        if merge_review_only_branch_names and handler.target_branch not in merge_review_only_branch_names:
            logger.info(f"Pull Request event, branch={handler.target_branch}, ignored.")
            return

        # 仅在PR创建或源分支更新时进行Code Review
        if handler.action not in ['opened', 'from_ref_updated']:
            logger.info(f"Pull Request event, action={handler.action}, ignored.")
            return

        # 获取Pull Request的changes
        changes = handler.get_pull_request_changes()
        logger.info('changes: %s', changes)
        changes = filter_bitbucket_changes(changes)
        if not changes:
            logger.info('未检测到有关代码的修改,修改文件可能不满足SUPPORTED_EXTENSIONS。')
            return
        # 统计本次新增、删除的代码总数
        additions = 0
        deletions = 0
        for item in changes:
            additions += item.get('additions', 0)
            deletions += item.get('deletions', 0)

        # 获取Pull Request的commits
        commits = handler.get_pull_request_commits()
        if not commits:
            logger.error('Failed to get commits')
            return

        # 读取仓库级指令文件（有界），拿不到不影响审查
        repo_context = handler.get_repo_context()

        # review 代码
        commits_text = ';'.join(commit.get('title', '') for commit in commits)
        reviewer = CodeReviewer()
        review_result = reviewer.review_and_strip_code(changes, commits_text, changes,
                                                       repo_context=repo_context)

        # 将review结果提交到Bitbucket的 comments（带上审查范围元信息）
        handler.add_pull_request_notes(reviewer.format_review_comment(review_result))

        # dispatch pull_request_reviewed event
        event_manager['merge_request_reviewed'].send(
            MergeRequestReviewEntity(
                project_name=handler.repository_name,
                author=handler.author,
                source_branch=handler.source_branch,
                target_branch=handler.target_branch,
                updated_at=int(datetime.now().timestamp()),
                commits=commits,
                score=CodeReviewer.parse_review_score(review_text=review_result),
                url=handler.pull_request_url,
                review_result=review_result,
                url_slug=bitbucket_url_slug,
                webhook_data=webhook_data,
                additions=additions,
                deletions=deletions,
            ))

    except Exception as e:
        error_message = f'AI Code Review 服务出现未知错误: {str(e)}\n{traceback.format_exc()}'
        _record_failed_review('mr', {
            'project_name': getattr(handler, 'repository_name', '') if handler else '',
            'author': getattr(handler, 'author', '') if handler else '',
            'source_branch': getattr(handler, 'source_branch', '') if handler else '',
            'target_branch': getattr(handler, 'target_branch', '') if handler else '',
            'commits': commits,
            'additions': additions,
            'deletions': deletions,
            'url': getattr(handler, 'pull_request_url', '') if handler else '',
            'webhook_data': webhook_data,
            'url_slug': bitbucket_url_slug,
            'platform': 'bitbucket',
        }, error_message)
        notifier.send_notification(content=error_message)
        logger.error('出现未知错误: %s', error_message)


def handle_bitbucket_push_event(webhook_data: dict, bitbucket_token: str, bitbucket_url: str,
                                bitbucket_url_slug: str):
    '''
    处理Bitbucket Server/Data Center Push(repo:refs_changed)事件
    :param webhook_data:
    :param bitbucket_token:
    :param bitbucket_url:
    :param bitbucket_url_slug:
    :return:
    '''
    push_review_enabled = os.environ.get('PUSH_REVIEW_ENABLED', '0') == '1'
    handler = None
    commits = []
    additions = 0
    deletions = 0
    try:
        handler = BitbucketPushHandler(webhook_data, bitbucket_token, bitbucket_url)
        logger.info('Bitbucket Push event received')
        commits = handler.get_push_commits()
        if not commits:
            logger.error('Failed to get commits')
            return

        review_result = None
        score = 0
        additions = 0
        deletions = 0
        if push_review_enabled:
            # 获取PUSH的changes
            changes = handler.get_push_changes()
            logger.info('changes: %s', changes)
            changes = filter_bitbucket_changes(changes)
            if not changes:
                logger.info('未检测到PUSH代码的修改,修改文件可能不满足SUPPORTED_EXTENSIONS。')
            review_result = "关注的文件没有修改"
            reviewer = CodeReviewer()

            if len(changes) > 0:
                commits_text = ';'.join(commit.get('message', '').strip() for commit in commits)
                review_result = reviewer.review_and_strip_code(changes, commits_text, changes)
                score = CodeReviewer.parse_review_score(review_text=review_result)
                for item in changes:
                    additions += item.get('additions', 0)
                    deletions += item.get('deletions', 0)
            # 将review结果提交到Bitbucket的 comments
            handler.add_push_notes(reviewer.format_review_comment(review_result))

        event_manager['push_reviewed'].send(PushReviewEntity(
            project_name=handler.repository_name,
            author=handler.author,
            branch=handler.branch_name,
            updated_at=int(datetime.now().timestamp()),  # 当前时间
            commits=commits,
            score=score,
            review_result=review_result,
            url_slug=bitbucket_url_slug,
            webhook_data=webhook_data,
            additions=additions,
            deletions=deletions,
            url=handler.push_url,
        ))

    except Exception as e:
        error_message = f'服务出现未知错误: {str(e)}\n{traceback.format_exc()}'
        _record_failed_review('push', {
            'project_name': getattr(handler, 'repository_name', '') if handler else '',
            'author': getattr(handler, 'author', '') if handler else '',
            'branch': getattr(handler, 'branch_name', '') if handler else '',
            'commits': commits,
            'additions': additions,
            'deletions': deletions,
            'url': getattr(handler, 'push_url', '') if handler else '',
            'webhook_data': webhook_data,
            'url_slug': bitbucket_url_slug,
            'platform': 'bitbucket',
        }, error_message)
        notifier.send_notification(content=error_message)
        logger.error('出现未知错误: %s', error_message)
