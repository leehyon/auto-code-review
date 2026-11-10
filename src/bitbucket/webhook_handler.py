"""
Bitbucket Server / Data Center webhook 处理模块。

与本项目其它平台的差异：
- 事件类型通过 ``X-Event-Key`` 请求头传递（``pr:opened`` / ``pr:from_ref_updated`` / ``repo:refs_changed`` 等），
  事件本身不会像 GitLab 那样放在 payload 的 ``object_kind`` 字段里。
- REST API 路径形如 ``/rest/api/1.0/projects/{projectKey}/repos/{repositorySlug}/...``，
  仓库使用 ``projectKey``（项目 Key）+ ``repositorySlug``（仓库 slug）标识。
- 认证统一使用 Bearer（``Authorization: Bearer <Personal Access Token>``），不传用户名。
- PR 的变更通过 ``pull-requests/{pullRequestId}.diff`` 返回原始 unified diff 文本，
  需要自行按文件切分后转换成 GitLab 风格的 changes 列表。
"""

import fnmatch
import hashlib
import hmac
import os
import re
import time
from typing import Optional
from urllib.parse import quote

import requests

from src.utils.file_filter import supported_extensions as get_supported_extensions
from src.utils.log import logger

# Bitbucket REST API 前缀。默认使用 1.0（兼容性最好），可通过环境变量覆盖为 /rest/api/latest。
DEFAULT_API_PATH = '/rest/api/1.0'
# 拉取/提交评论等接口的默认超时时间（秒）
DEFAULT_TIMEOUT = 30
# Push 审查时最多处理的 commit 数量与文件数量，避免超大推送拖垮审查任务
MAX_PUSH_COMMITS = 20
MAX_PUSH_FILES = 100
# 需要触发代码审查的 PR 事件
PR_REVIEW_EVENTS = ('pr:opened', 'pr:from_ref_updated')
# Bitbucket webhook 签名支持的哈希算法（WebSub 规范，Bitbucket 目前用 sha256）
WEBHOOK_SIGNATURE_ALGORITHMS = {
    'sha1': hashlib.sha1,
    'sha256': hashlib.sha256,
    'sha512': hashlib.sha512,
}


def get_api_path() -> str:
    """获取 Bitbucket REST API 前缀。"""
    return os.getenv('BITBUCKET_API_PATH', DEFAULT_API_PATH).rstrip('/')


def verify_webhook_signature(raw_body: bytes, signature: str, secret: str) -> bool:
    """
    校验 Bitbucket webhook 的 HMAC 签名。

    Bitbucket Data Center 在 webhook 配置了 Secret 之后，会在每次推送时带上
    ``X-Hub-Signature: sha256=<hex>``，其中 hex 是用 Secret 对**原始请求体**
    做 HMAC-SHA256 的结果。校验必须使用未经任何格式化/重新序列化的原始字节，
    否则签名对不上。

    参考：https://confluence.atlassian.com/bitbucketserver/manage-webhooks-1272382316.html

    :param raw_body: 原始请求体字节（不能是重新序列化过的 JSON）
    :param signature: X-Hub-Signature header 的值，形如 ``sha256=<hex>``
    :param secret: webhook secret
    :return: 签名有效返回 True
    """
    if not secret:
        logger.error('Bitbucket webhook secret is empty, cannot verify the signature.')
        return False
    if not raw_body:
        logger.error('Bitbucket webhook request body is empty, cannot verify the signature.')
        return False
    if not signature:
        logger.error('X-Hub-Signature header is missing from the Bitbucket webhook request.')
        return False

    method, _, digest = signature.partition('=')
    method = method.strip().lower()
    digest = digest.strip()
    if method not in WEBHOOK_SIGNATURE_ALGORITHMS or not digest:
        logger.error(f'Unsupported Bitbucket webhook signature format: {signature!r}. '
                     f'Expected something like "sha256=<hex>".')
        return False

    expected = hmac.new(secret.encode('utf-8'), raw_body,
                        WEBHOOK_SIGNATURE_ALGORITHMS[method]).hexdigest()
    if not hmac.compare_digest(expected, digest):
        # 打印前缀便于排查；HMAC 摘要本身不泄露 secret
        logger.error(f'Bitbucket webhook signature mismatch: method={method}, '
                     f'expected={expected[:12]}..., received={digest[:12]}... '
                     f'(body={len(raw_body)} bytes). Check that BITBUCKET_WEBHOOK_SECRET '
                     f'matches the secret configured in the Bitbucket webhook.')
        return False
    return True


# 默认从仓库根目录自动发现的约定文件（按价值排序，命中即读，受字节预算限制）。
# 多个项目不需要逐个配置；用 BITBUCKET_REPO_CONTEXT_FILES 可以覆盖成指定文件。
DEFAULT_REPO_CONTEXT_FILES = [
    'AGENTS.md',
    'CLAUDE.md',
    'CONTRIBUTING.md',
    '.github/copilot-instructions.md',
    '.cursorrules',
    '.clinerules',
    '.windsurfrules',
    'GEMINI.md',
    'README.md',
]


def filter_changes(changes: list) -> list:
    """
    过滤变更数据，只保留支持的文件类型以及必要的字段信息。

    与 GitLab/Gitea 的 filter_changes 保持一致的输出结构：
    ``{'diff', 'new_path', 'additions', 'deletions'}``

    ``type`` 和 ``old_path`` 是可选附加字段：有 type 才能把删除/重命名/二进制
    渲染成正确的 diff 段，否则它们会被当成普通修改，模型看到的东西和真实语义对不上。
    """
    supported_extensions = get_supported_extensions()

    filtered_changes = []
    for item in changes:
        new_path = item.get('new_path', '')
        if not new_path:
            continue

        # 只保留受支持的扩展名
        if not any(new_path.endswith(ext) for ext in supported_extensions):
            continue

        diff_content = item.get('diff', '')
        additions = item.get('additions', 0)
        deletions = item.get('deletions', 0)

        # API 未返回增删行数时，从 diff 中统计
        if not additions and not deletions and diff_content:
            additions = len(re.findall(r'^\+(?!\+\+)', diff_content, re.MULTILINE))
            deletions = len(re.findall(r'^-(?!-)', diff_content, re.MULTILINE))

        entry = {
            'diff': diff_content,
            'new_path': new_path,
            'additions': additions,
            'deletions': deletions,
        }
        if item.get('type'):
            entry['type'] = item['type']
        old_path = item.get('old_path')
        if old_path and old_path != new_path:
            entry['old_path'] = old_path
        filtered_changes.append(entry)

    logger.debug(f"filter_changes: filtered {len(filtered_changes)} files from {len(changes)} changes")
    return filtered_changes


def _strip_diff_prefix(path: str) -> str:
    """
    去掉 diff 中的路径前缀。

    Bitbucket Server/Data Center 使用 ``src://`` / ``dst://`` 前缀，
    标准 git diff 则使用 ``a/`` / ``b/`` 前缀，两者都需要处理。
    """
    if not path:
        return ''
    path = path.strip()
    if path.startswith('"') and path.endswith('"'):
        path = path[1:-1]
    if path == '/dev/null':
        return ''
    for prefix in ('src://', 'dst://'):
        if path.startswith(prefix):
            return path[len(prefix):]
    for prefix in ('a/', 'b/'):
        if path.startswith(prefix):
            return path[len(prefix):]
    return path


def _strip_repo_prefix(path: str) -> str:
    """
    去掉仓库相对路径上的 diff 协议前缀。

    git 的 ``rename from`` / ``rename to`` 行给的是仓库相对路径（不带 a/ b/ 前缀），
    用 _strip_diff_prefix 会把目录名恰好以 a/ 或 b/ 开头的路径剥错，
    所以这里只处理 src:// / dst:// 和引号。
    """
    if not path:
        return ''
    path = path.strip()
    if path.startswith('"') and path.endswith('"'):
        path = path[1:-1]
    if path == '/dev/null':
        return ''
    for prefix in ('src://', 'dst://'):
        if path.startswith(prefix):
            return path[len(prefix):]
    return path


def _split_diff_git_header(line: str) -> tuple:
    """
    从 ``diff --git <old> <new>`` 头里取出旧路径和新路径。

    Bitbucket DC 的头是 ``diff --git src://X dst://X``，两段带不同前缀。
    按第一个空格切会把带空格的目录名切错，所以先按 dst:// 前缀定位；
    纯 git 形式（``a/... b/...``）再走通用分支。
    """
    rest = line[len('diff --git '):]
    idx = rest.find(' dst://')
    if idx != -1 and rest.startswith('src://'):
        return _strip_diff_prefix(rest[:idx]), _strip_diff_prefix(rest[idx + 1:])
    match = re.match(r'^(a/.+?) (b/.+)$', rest)
    if match:
        return _strip_diff_prefix(match.group(1)), _strip_diff_prefix(match.group(2))
    parts = rest.split(' ', 1)
    if len(parts) == 2:
        return _strip_diff_prefix(parts[0]), _strip_diff_prefix(parts[1])
    return '', ''


def parse_unified_diff(raw_diff: Optional[str] = None) -> list:
    """
    把 Bitbucket 返回的原始 unified diff 文本切分成按文件组织的变更列表。

    兼容两种格式：
    - 带 ``diff --git a/xxx b/xxx`` 头的标准 git diff
    - 只有 ``--- a/xxx`` / ``+++ b/xxx`` 头的精简 diff（部分 Bitbucket 版本）

    **不丢弃任何 section**：删除的文件、纯重命名、二进制文件都会保留成显式条目
    （type 分别为 DELETE / RENAME / BINARY）。丢掉它们的话模型完全不知道本次变更里
    有过这些文件——删掉一个端口定义和删掉整个 arxml 文件在 diff 正文里长得几乎一样，
    语义却完全相反。

    :param raw_diff: unified diff 文本
    :return: ``[{'old_path', 'new_path', 'diff', 'additions', 'deletions', 'type'}]``
    """
    if not raw_diff:
        return []

    sections = []
    current = None
    for line in raw_diff.split('\n'):
        if line.startswith('diff --git '):
            current = []
            sections.append(current)
            # 头本身要留在 section 里：二进制文件和纯重命名的路径只能从它取
            current.append(line)
            continue
        # 没有 diff --git 头时，用 --- 行作为新文件的开始
        if line.startswith('--- ') and (current is None or not current):
            current = []
            sections.append(current)
        if current is not None:
            current.append(line)

    changes = []
    for section in sections:
        header_old = header_new = ''
        old_path = ''
        new_path = ''
        rename_from = ''
        rename_to = ''
        new_file_mode = False
        deleted_file_mode = False
        body_started = False
        body_lines = []

        for line in section:
            if body_started:
                body_lines.append(line)
                continue
            if line.startswith('diff --git '):
                header_old, header_new = _split_diff_git_header(line)
            elif line.startswith('rename from '):
                rename_from = _strip_repo_prefix(line[len('rename from '):])
            elif line.startswith('rename to '):
                rename_to = _strip_repo_prefix(line[len('rename to '):])
            elif line.startswith('new file mode'):
                new_file_mode = True
            elif line.startswith('deleted file mode'):
                deleted_file_mode = True
            elif line.startswith('--- '):
                old_path = _strip_diff_prefix(line[4:])
            elif line.startswith('+++ '):
                new_path = _strip_diff_prefix(line[4:])
            elif line.startswith('@@'):
                body_started = True
                body_lines.append(line)
            # index / similarity index / mode 等头部信息忽略

        body = '\n'.join(body_lines)
        additions = len(re.findall(r'^\+(?!\+\+)', body, re.MULTILINE))
        deletions = len(re.findall(r'^-(?!-)', body, re.MULTILINE))

        # ---- 判定变更类型 ----
        if rename_from or rename_to:
            change_type = 'RENAME'
            old_path = rename_from or old_path or header_old
            new_path = rename_to or new_path or header_new or old_path
            body = f'rename from {old_path}\nrename to {new_path}'
            additions = deletions = 0
        elif deleted_file_mode or (old_path and not new_path):
            # 文件被删除：保留 - 行内容，模型需要知道被删掉了什么
            change_type = 'DELETE'
            new_path = old_path
        elif new_file_mode or (new_path and not old_path):
            change_type = 'ADD'
            old_path = new_path
        elif not old_path and not new_path:
            # 既没有 --- / +++ 也没有 rename：二进制文件
            change_type = 'BINARY'
            old_path = header_old
            new_path = header_new or header_old
            body = 'Binary files differ (二进制文件，无法以文本形式审查)'
            additions = deletions = 0
        else:
            change_type = 'MODIFY'

        if not new_path and not old_path:
            # 连 diff --git 头都解析不出来，无法确定是哪个文件，跳过
            continue

        changes.append({
            'old_path': old_path or new_path,
            'new_path': new_path or old_path,
            'diff': body,
            'additions': additions,
            'deletions': deletions,
            'type': change_type,
        })

    logger.debug(f"parse_unified_diff: parsed {len(changes)} files from raw diff")
    return changes


def rest_diff_to_unified(diff_data: dict) -> str:
    """
    把 ``RestDiff`` 结构的 JSON 转换成 unified diff 文本。

    用于 ``diff/{path}`` 这类逐文件返回 JSON 的接口：
    ``hunks[] -> segments[] -> lines[]``，segment 的 type 为 ADDED / CONTEXT / REMOVED。
    """
    if not diff_data:
        return ''

    lines = []
    for hunk in diff_data.get('hunks', []) or []:
        source_line = hunk.get('sourceLine', 0)
        source_span = hunk.get('sourceSpan', 0)
        destination_line = hunk.get('destinationLine', 0)
        destination_span = hunk.get('destinationSpan', 0)
        lines.append(f"@@ -{source_line},{source_span} +{destination_line},{destination_span} @@")
        for segment in hunk.get('segments', []) or []:
            segment_type = segment.get('type', 'CONTEXT')
            for line in segment.get('lines', []) or []:
                text = line.get('line', '')
                if segment_type == 'ADDED':
                    lines.append(f"+{text}")
                elif segment_type == 'REMOVED':
                    lines.append(f"-{text}")
                else:
                    lines.append(f" {text}")
    return '\n'.join(lines)


def path_to_string(path_obj: dict) -> str:
    """把 Bitbucket 的 path 结构（components/name/parent）转换成字符串路径。"""
    if not path_obj:
        return ''
    components = path_obj.get('components')
    if components:
        return '/'.join(components)
    parent = path_obj.get('parent', '')
    name = path_obj.get('name', '')
    return f"{parent}/{name}" if parent else name


class BitbucketClient:
    """Bitbucket Server / Data Center REST 客户端。

    认证统一使用 Bearer：``Authorization: Bearer <token>``。

    只传 token，不传用户名，理由如下：
    - Bitbucket 的个人 token（Manage account -> HTTP access tokens）和项目/仓库级 token
      （Project/Repository settings -> HTTP access tokens）都支持 Bearer；
      官方规定项目/仓库级 token *只能* 用 Bearer。
    - HTTP Basic 需要把用户名和 token 一起编码传输，相比 Bearer 并不更安全，
      而且部分 Atlassian 部署的机器人防护会针对 Basic 认证触发 CAPTCHA，令自动化调用中断。
    - 不需要用户名，也就不需要在配置里保存用户名。

    参考：https://confluence.atlassian.com/enterprise/using-personal-access-tokens-1026032365.html
    """

    def __init__(self, base_url: str, token: str):
        self.base_url = (base_url or '').rstrip('/')
        self.token = token or ''
        # 自建实例通常使用自签证书，默认关闭校验，可用 BITBUCKET_VERIFY_SSL=1 开启
        self.verify = os.getenv('BITBUCKET_VERIFY_SSL', '0') == '1'

    def _auth_headers(self, accept: str = 'application/json') -> dict:
        """构造请求头，统一使用 Bearer 认证。"""
        headers = {
            'Accept': accept,
            'Content-Type': 'application/json',
        }
        if self.token:
            headers['Authorization'] = f'Bearer {self.token}'
        return headers

    @property
    def headers(self) -> dict:
        return self._auth_headers()

    def api_url(self, path: str) -> str:
        """拼接 REST API 1.0 的完整地址。"""
        return f"{self.base_url}{get_api_path()}{path}"

    def plugin_url(self, plugin: str, path: str) -> str:
        """拼接插件 REST 接口地址，例如 branch-permissions。"""
        return f"{self.base_url}/rest/{plugin}/latest{path}"

    def get(self, path: str, params: dict = None, plugin: str = None,
            accept: str = 'application/json') -> requests.Response:
        url = self.plugin_url(plugin, path) if plugin else self.api_url(path)
        logger.debug(f"Bitbucket GET {url}, params={params}")
        return requests.get(url, headers=self._auth_headers(accept=accept),
                            params=params or {}, verify=self.verify, timeout=DEFAULT_TIMEOUT)

    def get_text(self, path: str, params: dict = None) -> Optional[str]:
        """
        GET 请求并返回原始文本（用于 .diff / diff 这类返回文本的接口）。

        注意：Bitbucket 的 .diff 接口只接受 text/plain，用 application/json 请求会返回 406。

        请求失败返回 None，接口正常但内容为空返回空字符串，调用方据此区分重试。
        """
        response = self.get(path, params=params, accept='text/plain')
        if response.status_code != 200:
            logger.warn(f"Failed to get raw diff from Bitbucket: {response.status_code}, {response.text[:200]}")
            return None
        return response.text

    def get_file_content(self, path: str, ref: Optional[str] = None) -> Optional[str]:
        """
        按完整 REST 路径取仓库文件原文（/raw 端点）。

        用于读取仓库级指令文件（如 AGENTS.md）。文件不存在返回 None，
        接口异常也返回 None——调用方据此决定跳过，不能因为取不到就中断整个审查。
        """
        params = {'at': ref} if ref else {}
        response = self.get(path, params=params, accept='text/plain')
        if response.status_code == 404:
            # 自动发现约定文件时会探测一串候选名，404 是常态，用 debug 级别
            logger.debug(f"Bitbucket file not found: {path}")
            return None
        if response.status_code != 200:
            logger.warning(f"Failed to get file content from Bitbucket: "
                           f"{response.status_code}, {response.text[:200]}")
            return None
        return response.text

    def get_json(self, path: str, params: dict = None, plugin: str = None) -> dict:
        """GET 请求并返回 JSON。"""
        response = self.get(path, params=params, plugin=plugin)
        if response.status_code != 200:
            logger.warn(f"Failed to get data from Bitbucket: {response.status_code}, {response.text[:200]}")
            return {}
        try:
            return response.json()
        except Exception as e:
            logger.error(f"Failed to parse Bitbucket response as JSON: {e}")
            return {}

    def post(self, path: str, payload: dict) -> requests.Response:
        url = self.api_url(path)
        logger.debug(f"Bitbucket POST {url}")
        return requests.post(url, headers=self._auth_headers(), json=payload, verify=self.verify,
                             timeout=DEFAULT_TIMEOUT)


class PullRequestHandler:
    """Bitbucket Server / Data Center Pull Request 事件处理器。"""

    def __init__(self, webhook_data: dict, bitbucket_token: str, bitbucket_url: str):
        self.webhook_data = webhook_data
        self.client = BitbucketClient(bitbucket_url, bitbucket_token)
        self.event_type = None
        self.action = None
        self.pull_request_id = None
        self.project_key = None
        self.repository_slug = None
        self.repository_name = None
        self.repo_full_name = None
        self.source_branch = None
        self.target_branch = None
        self.source_commit = None
        self.target_commit = None
        self.author = None
        self.pull_request_url = ''
        self.parse_event_type()

    def parse_event_type(self):
        self.event_type = self.webhook_data.get('eventKey', None)
        if self.event_type in PR_REVIEW_EVENTS:
            self.parse_pull_request_event()

    def parse_pull_request_event(self):
        pull_request = self.webhook_data.get('pullRequest', {}) or {}
        self.pull_request_id = pull_request.get('id')
        self.action = self.event_type.split(':', 1)[1] if self.event_type else None

        from_ref = pull_request.get('fromRef', {}) or {}
        to_ref = pull_request.get('toRef', {}) or {}
        self.source_branch = from_ref.get('displayId', '')
        self.target_branch = to_ref.get('displayId', '')
        self.source_commit = from_ref.get('latestCommit', '')
        self.target_commit = to_ref.get('latestCommit', '')

        # PR 归属于目标仓库（toRef），跨仓库 PR 的源仓库信息只用于展示
        repository = to_ref.get('repository') or from_ref.get('repository') or {}
        project = repository.get('project', {}) or {}
        self.project_key = project.get('key', '')
        self.repository_slug = repository.get('slug', '')
        self.repository_name = repository.get('name', '')
        if self.project_key and self.repository_slug:
            self.repo_full_name = f"{self.project_key}/{self.repository_slug}"

        author = pull_request.get('author', {}) or {}
        user = author.get('user', {}) if isinstance(author, dict) else {}
        self.author = user.get('name') or user.get('displayName') or user.get('slug') or ''

        links = pull_request.get('links', {}) or {}
        self_links = links.get('self') or []
        if self_links and isinstance(self_links, list) and self_links[0]:
            self.pull_request_url = self_links[0].get('href', '')
        if not self.pull_request_url and self.repo_full_name:
            # payload 中没有 self link 时，用标准浏览地址兜底
            self.pull_request_url = (f"{self.client.base_url}/projects/{self.project_key}"
                                     f"/repos/{self.repository_slug}/pull-requests/{self.pull_request_id}")

        logger.debug(f"Parsed Bitbucket PR event: id={self.pull_request_id}, repo={self.repo_full_name}, "
                     f"source={self.source_branch}, target={self.target_branch}, action={self.action}")

    @property
    def pr_api_path(self) -> str:
        return f"/projects/{self.project_key}/repos/{self.repository_slug}/pull-requests/{self.pull_request_id}"

    def get_pull_request_changes(self) -> list:
        """
        获取 PR 的代码变更。

        使用 ``pull-requests/{id}.diff`` 接口一次性拿到全部 diff（一次请求），
        再按文件切分成统一格式的 changes 列表。
        """
        if self.event_type not in PR_REVIEW_EVENTS:
            logger.warn(f"Invalid event type: {self.event_type}. "
                        f"Only {PR_REVIEW_EVENTS} events are supported now.")
            return []

        # 与其它平台保持一致：PR 的变更接口可能存在延迟，多次尝试
        max_retries = 3
        retry_delay = 10
        for attempt in range(max_retries):
            raw_diff = self.client.get_text(f"{self.pr_api_path}.diff")
            if raw_diff is None:
                # 请求失败，重试
                logger.info(f"Failed to get raw diff, retrying in {retry_delay} seconds... "
                            f"(attempt {attempt + 1}/{max_retries})")
            elif raw_diff:
                changes = parse_unified_diff(raw_diff)
                if changes:
                    return changes
                # diff 有内容但没有可审查的文件，视为无变更，不再重试
                logger.info("Raw diff retrieved but no reviewable file found.")
                return []
            else:
                # PR 本身没有代码变更，不需要重试
                logger.info("Pull request has no diff, skip review.")
                return []
            time.sleep(retry_delay)

        logger.warning(f"Max retries ({max_retries}) reached. Changes is still empty.")
        return []

    def get_pull_request_commits(self) -> list:
        """获取 PR 包含的 commit 列表（转换为 GitLab 风格的统一结构）。"""
        if self.event_type not in PR_REVIEW_EVENTS:
            return []

        commits = []
        start = 0
        limit = 100
        while True:
            page = self.client.get_json(f"{self.pr_api_path}/commits", params={'start': start, 'limit': limit})
            values = page.get('values', []) or []
            commits.extend(values)
            if not values or page.get('isLastPage', True):
                break
            start = page.get('nextPageStart', start + limit)
            if len(commits) > 1000:
                logger.warn("Too many commits in pull request, stop paging.")
                break

        unified_commits = []
        for commit in commits:
            message = commit.get('message', '') or ''
            author = commit.get('author', {}) or {}
            unified_commits.append({
                'id': commit.get('id', ''),
                'title': message.split('\n')[0],
                'message': message,
                'author_name': author.get('name', ''),
                'author_email': author.get('emailAddress', ''),
                'created_at': commit.get('authorTimestamp', ''),
                'web_url': self._build_commit_url(commit.get('id', '')),
            })
        return unified_commits

    def _build_commit_url(self, commit_id: str) -> str:
        if not commit_id:
            return ''
        return f"{self.client.base_url}/projects/{self.project_key}/repos/{self.repository_slug}/commits/{commit_id}"

    def get_repo_context(self) -> str:
        """
        读取仓库级指令文件，有界地返回文本。

        **默认从仓库根目录自动发现**常见约定文件（AGENTS.md、CONTRIBUTING.md 等），
        所以多个项目不需要逐个配置；配置 ``BITBUCKET_REPO_CONTEXT_FILES`` 则改为
        只读指定的文件（用于覆盖自动发现的清单）。

        文件从 PR 的**目标分支**读取：约定文件应该是稳定的，源分支的内容由提交者
        控制，不适合作为审查依据。

        读取失败（文件不存在、权限不足、网络问题）都跳过——拿不到仓库约定
        不应该让整个审查失败。
        """
        override = [f.strip() for f in
                    os.getenv('BITBUCKET_REPO_CONTEXT_FILES', '').split(',') if f.strip()]
        if override:
            files = override
            auto_discovered = False
        else:
            files = list(DEFAULT_REPO_CONTEXT_FILES)
            auto_discovered = True

        try:
            max_lines = int(os.getenv('BITBUCKET_REPO_CONTEXT_MAX_LINES', '200'))
            max_bytes = int(os.getenv('BITBUCKET_REPO_CONTEXT_MAX_BYTES', '20000'))
        except ValueError:
            max_lines, max_bytes = 200, 20000

        blocks = []
        used_bytes = 0
        found = []
        for rel in files:
            if used_bytes >= max_bytes:
                break
            try:
                content = self.client.get_file_content(
                    f"/projects/{self.project_key}/repos/{self.repository_slug}/raw/{rel}",
                    ref=self.target_branch or None)
            except Exception as e:
                logger.warning(f"Failed to read repo context file {rel}: {e}")
                continue
            if not content:
                if not auto_discovered:
                    logger.warning(f"Repo context file not found: {rel}")
                continue
            found.append(rel)

            lines = content.splitlines()
            truncated = len(lines) > max_lines
            lines = lines[:max_lines]
            text = '\n'.join(lines)
            remaining = max_bytes - used_bytes
            encoded = text.encode('utf-8')
            if len(encoded) > remaining:
                text = encoded[:remaining].decode('utf-8', 'ignore')
                truncated = True
            used_bytes += len(text.encode('utf-8'))

            note = '（内容过长，已截断）' if truncated else ''
            blocks.append(f"### {rel}{note}\n{text}")

        if blocks:
            logger.info(f"Repo context loaded from {found} "
                        f"({len(blocks)} file(s), {used_bytes} bytes)")
        return '\n\n'.join(blocks)

    def add_pull_request_notes(self, review_result: str):
        """把审查结果以评论形式写回 PR。"""
        if not self.pull_request_id:
            logger.error("pull_request_id is empty, cannot add comment")
            return
        if not review_result:
            logger.warn("review_result is empty, skipping comment")
            return

        payload = {'text': review_result}
        response = self.client.post(f"{self.pr_api_path}/comments", payload)
        logger.debug(f"Add comment to Bitbucket PR {self.pr_api_path}/comments: "
                     f"{response.status_code}, {response.text[:200]}")
        if response.status_code in (200, 201):
            logger.info("Comment successfully added to pull request.")
        else:
            logger.error(f"Failed to add comment: {response.status_code}")
            logger.error(response.text[:500])

    def target_branch_protected(self) -> bool:
        """
        判断 PR 的目标分支是否配置了分支限制（受保护分支）。

        该接口要求 REPO_ADMIN 权限，调用失败时按“非受保护分支”处理。
        """
        if not self.project_key or not self.repository_slug:
            return False

        path = f"/projects/{self.project_key}/repos/{self.repository_slug}/restrictions"
        page = self.client.get_json(path, plugin='branch-permissions')
        restrictions = page.get('values', []) or []
        if not restrictions:
            logger.warn("No branch restrictions found, treat target branch as unprotected.")
            return False

        for restriction in restrictions:
            matcher = restriction.get('matcher', {}) or {}
            pattern = matcher.get('displayId') or matcher.get('id', '')
            if not pattern:
                continue
            # refs/heads/master -> master
            if pattern.startswith('refs/heads/'):
                pattern = pattern[len('refs/heads/'):]
            if self.target_branch and fnmatch.fnmatch(self.target_branch, pattern):
                return True
        return False


class PushHandler:
    """Bitbucket Server / Data Center Push（repo:refs_changed）事件处理器。"""

    def __init__(self, webhook_data: dict, bitbucket_token: str, bitbucket_url: str):
        self.webhook_data = webhook_data
        self.client = BitbucketClient(bitbucket_url, bitbucket_token)
        self.event_type = None
        self.project_key = None
        self.repository_slug = None
        self.repository_name = None
        self.repo_full_name = None
        self.branch_name = ''
        self.author = None
        self.commit_list = []
        self.parse_event_type()

    def parse_event_type(self):
        self.event_type = self.webhook_data.get('eventKey', None)
        if self.event_type == 'repo:refs_changed':
            self.parse_push_event()

    def parse_push_event(self):
        repository = self.webhook_data.get('repository', {}) or {}
        project = repository.get('project', {}) or {}
        self.project_key = project.get('key', '')
        self.repository_slug = repository.get('slug', '')
        self.repository_name = repository.get('name', '')
        if self.project_key and self.repository_slug:
            self.repo_full_name = f"{self.project_key}/{self.repository_slug}"

        actor = self.webhook_data.get('actor', {}) or {}
        self.author = actor.get('name') or actor.get('displayName') or actor.get('slug') or ''

        # 一个推送事件可能包含多个 ref，只处理分支（跳过 tag）
        changes = self.webhook_data.get('changes', []) or []
        for change in changes:
            ref = change.get('ref', {}) or {}
            if ref.get('type') == 'TAG':
                continue
            self.branch_name = ref.get('displayId', '')
            break

        self.commit_list = self.webhook_data.get('commits', []) or []

    @property
    def repo_api_path(self) -> str:
        return f"/projects/{self.project_key}/repos/{self.repository_slug}"

    def get_push_commits(self) -> list:
        """获取本次推送的 commit 列表（转换为统一结构）。"""
        if self.event_type != 'repo:refs_changed':
            logger.warn(f"Invalid event type: {self.event_type}. Only 'repo:refs_changed' event is supported now.")
            return []

        commits = self.commit_list
        if not commits:
            # payload 中的 commits 可能被截断（默认最多 5 个），回退到 API 查询
            commits = self._fetch_commits_from_api()

        commit_details = []
        for commit in commits:
            author = commit.get('author', {}) or {}
            commit_details.append({
                'id': commit.get('id', ''),
                'title': (commit.get('message', '') or '').split('\n')[0],
                'message': commit.get('message', '') or '',
                'author': author.get('name', ''),
                'timestamp': commit.get('authorTimestamp', ''),
                'url': self._build_commit_url(commit.get('id', '')),
            })

        logger.info(f"Collected {len(commit_details)} commits from push event.")
        return commit_details

    def _fetch_commits_from_api(self) -> list:
        """通过 API 查询推送区间内的 commit（payload 中 commits 被截断时使用）。"""
        params = {'limit': MAX_PUSH_COMMITS}
        for change in self._branch_changes():
            if change.get('fromHash') and not change['fromHash'].startswith('0000'):
                params['since'] = change['fromHash']
            if change.get('toHash'):
                params['until'] = change['toHash']
            break

        page = self.client.get_json(f"{self.repo_api_path}/commits", params=params)
        return page.get('values', []) or []

    def _branch_changes(self) -> list:
        """返回本次推送中涉及分支（而非 tag）的变更列表。"""
        changes = self.webhook_data.get('changes', []) or []
        branch_changes = []
        for change in changes:
            ref = change.get('ref', {}) or {}
            if ref.get('type') == 'TAG':
                continue
            branch_changes.append(change)
        return branch_changes

    @property
    def push_url(self) -> str:
        """本次推送 head commit 的浏览器链接（用于 Dashboard 的提交链接列）。

        toHash 就是被推送分支的 head commit。
        """
        for change in self._branch_changes():
            to_hash = change.get('toHash')
            if to_hash:
                return self._build_commit_url(to_hash)
        return ''

    def _build_commit_url(self, commit_id: str) -> str:
        if not commit_id:
            return ''
        return f"{self.client.base_url}/projects/{self.project_key}/repos/{self.repository_slug}/commits/{commit_id}"

    def get_push_changes(self) -> list:
        """获取本次推送涉及的文件变更。"""
        if self.event_type != 'repo:refs_changed':
            logger.warn(f"Invalid event type: {self.event_type}. Only 'repo:refs_changed' event is supported now.")
            return []

        commits = self.commit_list or self._fetch_commits_from_api()
        if not commits:
            logger.info("No commits found in push event.")
            return []

        # 优先使用仓库级 raw diff 接口（一次请求拿到整个推送区间的 diff）
        raw_diff = self._get_repo_range_diff(commits)
        if raw_diff:
            changes = parse_unified_diff(raw_diff)
            if changes:
                logger.info(f"Found {len(changes)} file changes in push event")
                return changes

        # 回退方案：逐个 commit、逐个文件获取 diff
        changes = []
        seen_paths = set()
        for commit in commits[:MAX_PUSH_COMMITS]:
            commit_id = commit.get('id', '')
            if not commit_id:
                continue
            parents = commit.get('parents', []) or []
            parent_id = parents[0].get('id', '') if parents else ''

            for change in self._get_commit_file_changes(commit_id, parent_id):
                path = change.get('new_path', '')
                if path in seen_paths:
                    continue
                seen_paths.add(path)
                changes.append(change)
                if len(changes) >= MAX_PUSH_FILES:
                    logger.warn(f"Too many changed files ({MAX_PUSH_FILES}), stop collecting.")
                    return changes

        logger.info(f"Found {len(changes)} file changes in push event")
        return changes

    def _get_repo_range_diff(self, commits: list) -> str:
        """使用仓库级 diff 接口获取推送区间的原始 diff。"""
        from_hash = ''
        to_hash = ''
        for change in self._branch_changes():
            from_hash = change.get('fromHash', '') or ''
            to_hash = change.get('toHash', '') or ''
            break

        if not to_hash:
            to_hash = commits[-1].get('id', '') if commits else ''

        # 新建分支没有 fromHash，退化为与首个 commit 的父提交比较
        if not from_hash or from_hash.startswith('0000'):
            parents = commits[0].get('parents', []) or [] if commits else []
            from_hash = parents[0].get('id', '') if parents else ''

        if not to_hash:
            return ''

        params = {'until': to_hash}
        if from_hash:
            params['since'] = from_hash
        return self.client.get_text(f"{self.repo_api_path}/diff", params=params) or ''

    def _get_commit_file_changes(self, commit_id: str, parent_id: str) -> list:
        """获取单个 commit 的文件变更。"""
        # 优先使用 commit 级别的原始 diff 接口（一次请求拿到整个 commit 的 diff）
        raw_diff = self.client.get_text(f"{self.repo_api_path}/commits/{commit_id}/diff")
        if raw_diff:
            return parse_unified_diff(raw_diff)

        # 回退方案：逐个文件获取 diff
        changes = []
        page = self.client.get_json(f"{self.repo_api_path}/commits/{commit_id}/changes")
        for change in page.get('values', []) or []:
            path = path_to_string(change.get('path', {}))
            if not path:
                continue
            if change.get('type') == 'DELETE':
                continue

            diff_text = ''
            encoded_path = quote(path, safe='/')
            params = {'since': parent_id} if parent_id else {}
            diff_data = self.client.get_json(
                f"{self.repo_api_path}/commits/{commit_id}/diff/{encoded_path}", params=params
            )
            if diff_data:
                diff_text = rest_diff_to_unified(diff_data)

            changes.append({
                'old_path': path_to_string(change.get('srcPath', {})) or path,
                'new_path': path,
                'diff': diff_text,
                'additions': 0,
                'deletions': 0,
                'type': change.get('type', 'MODIFY'),
            })
        return changes

    def add_push_notes(self, message: str):
        """把审查结果以评论形式写回本次推送的最后一个 commit。"""
        if not self.commit_list:
            logger.warn("No commits found to add notes to.")
            return

        last_commit_id = self.commit_list[-1].get('id', '')
        if not last_commit_id:
            logger.error("Last commit ID not found.")
            return

        payload = {'text': message}
        response = self.client.post(f"{self.repo_api_path}/commits/{last_commit_id}/comments", payload)
        logger.debug(f"Add comment to Bitbucket commit {last_commit_id}: "
                     f"{response.status_code}, {response.text[:200]}")
        if response.status_code in (200, 201):
            logger.info("Comment successfully added to push commit.")
        else:
            logger.error(f"Failed to add comment: {response.status_code}")
            logger.error(response.text[:500])
