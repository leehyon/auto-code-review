import os
import time
import re
import requests
from urllib.parse import urljoin
from src.utils.log import logger


def filter_changes(changes: list):
    """Filter Bitbucket changes into the common format used by reviewers.
    Expected output items: {'diff': str, 'new_path': str, 'additions': int, 'deletions': int}
    """
    # normalize supported extensions from env (strip whitespace, ignore empties)
    supported_extensions = [ext.strip() for ext in os.getenv('SUPPORTED_EXTENSIONS', '.java,.py,.php').split(',') if ext.strip()]
    filtered = []
    for item in changes:
        # Bitbucket change formats vary; attempt to locate file path and diff
        new_path = ''
        # common shapes
        if isinstance(item.get('path'), dict):
            new_path = item['path'].get('toString') or item['path'].get('to') or ''
        new_path = new_path or item.get('newPath') or item.get('new_path') or item.get('path') or item.get('filePath') or ''

        diff = item.get('diff') or item.get('patch') or item.get('content') or ''

        # if path still missing, try to extract from unified diff text
        if not new_path and diff:
            lines = diff.splitlines()
            first_line = lines[0] if lines else ''
            # try several diff header formats: src://dst://, a/... b/..., fallback to +++ line
            m = re.search(r"diff --git\s+(?:src://)?(.+?)\s+(?:dst://)?(.+)$", first_line)
            if m:
                new_path = m.group(2)
            else:
                m = re.search(r"diff --git a/(.+?) b/(.+)$", first_line)
                if m:
                    new_path = m.group(2)
                else:
                    m2 = re.search(r"\+\+\+\s+(?:dst://|b/)?(.+)$", diff, re.MULTILINE)
                    if m2:
                        new_path = m2.group(1)

        # normalize common prefixes
        if isinstance(new_path, str):
            if new_path.startswith('a/') or new_path.startswith('b/'):
                new_path = new_path[2:]
            new_path = new_path.lstrip('./')

        if not new_path:
            # give up if we still don't have a path
            continue

        # skip by extension
        if not any(new_path.endswith(ext) for ext in supported_extensions):
            continue

        # estimate additions/deletions
        additions = item.get('additions', 0)
        deletions = item.get('deletions', 0)
        if (additions == 0 and deletions == 0) and diff:
            additions = len(re.findall(r'^\+(?!\+\+)', diff, re.MULTILINE))
            deletions = len(re.findall(r'^-(?!--)', diff, re.MULTILINE))

        # detect deletions and skip them
        status = (item.get('status') or item.get('changeType') or '').lower()
        deleted_via_status = status == 'removed' or status == 'deleted'
        deleted_via_diff = False
        if diff:
            first = diff.splitlines()[0] if diff.splitlines() else ''
            if re.match(r'@@ -\d+,\d+ \+0,0 @@', first):
                # likely a deletion
                deleted_via_diff = True

        if deleted_via_status or deleted_via_diff:
            logger.info(f"Detected deleted file, skipping: {new_path}")
            continue

        filtered.append({
            'diff': diff,
            'new_path': new_path,
            'additions': additions,
            'deletions': deletions
        })
    logger.debug(f"Bitbucket filter_changes -> {len(filtered)} files")
    return filtered


class PullRequestHandler:
    def __init__(self, webhook_data: dict, bitbucket_token: str, bitbucket_url: str):
        self.webhook_data = webhook_data
        self.bitbucket_token = bitbucket_token
        self.bitbucket_url = bitbucket_url.rstrip('/')
        self.pull_request_id = None
        self.repo_project = None
        self.repo_slug = None
        self.repo_full_name = None
        self.action = None
        self.parse_event()

    def parse_event(self):
        pr = self.webhook_data.get('pullRequest') or self.webhook_data.get('pull_request') or {}
        # pull request id may appear under several keys depending on Bitbucket version/gateway
        self.pull_request_id = pr.get('id') or pr.get('pullRequestId') or pr.get('number') or None

        # repository info may be provided at top-level or under toRef/fromRef in the PR payload
        repository = (self.webhook_data.get('repository')
                      or pr.get('toRef', {}).get('repository')
                      or pr.get('fromRef', {}).get('repository')
                      or {})

        # project can be a dict ({'key': 'PROJ'}) or sometimes a plain string/projectKey
        repo_project = None
        project = repository.get('project')
        if isinstance(project, dict):
            repo_project = project.get('key')
        elif isinstance(project, str):
            repo_project = project
        # fallback to explicit projectKey field
        if not repo_project:
            repo_project = repository.get('projectKey')

        # repo slug/name
        repo_slug = repository.get('slug') or repository.get('name') or repository.get('repo_slug')
        # if full_name like 'PROJ/repo' is provided, derive missing pieces
        full_name = repository.get('full_name') or repository.get('fullName')
        if (not repo_project or not repo_slug) and isinstance(full_name, str) and '/' in full_name:
            parts = full_name.split('/')
            if len(parts) >= 2:
                repo_project = repo_project or parts[0]
                repo_slug = repo_slug or parts[1]

        self.repo_project = repo_project
        self.repo_slug = repo_slug
        if self.repo_project and self.repo_slug:
            self.repo_full_name = f"{self.repo_project}/{self.repo_slug}"

        # action may be provided by API gateway
        self.action = self.webhook_data.get('action') or ''

    def _auth_headers(self):
        return {'Authorization': f'Bearer {self.bitbucket_token}', 'Content-Type': 'application/json'}

    def get_pull_request_changes(self) -> list:
        if not self.pull_request_id or not (self.repo_project and self.repo_slug):
            # attempt from webhook structure
            pr = self.webhook_data.get('pullRequest') or self.webhook_data.get('pull_request') or {}
            changes = pr.get('changes') or pr.get('properties') or []
            return changes

        changes = []
        try:
            # First try to fetch the unified diff text for the PR (recommended by Bitbucket Server)
            diff_url = f"{self.bitbucket_url}/rest/api/latest/projects/{self.repo_project}/repos/{self.repo_slug}/pull-requests/{self.pull_request_id}.diff"
            headers = self._auth_headers()
            # request plain text diff
            headers.update({'Accept': 'text/plain'})
            r = requests.get(diff_url, headers=headers, timeout=20, verify=False)
            logger.debug(f"Bitbucket get .diff status: {r.status_code} for {diff_url}")
            if r.status_code == 200 and r.text:
                full_diff = r.text
                # split into per-file diffs by 'diff --git' headers
                parts = re.split(r"\n(?=diff --git )", full_diff)
                for part in parts:
                    part = part.strip('\n')
                    if not part:
                        continue
                    # try to extract new file path from the diff header
                    first_line = part.split('\n', 1)[0]
                    new_path = ''
                    m = re.search(r"diff --git a/(.+?) b/(.+)$", first_line)
                    if m:
                        new_path = m.group(2)
                    else:
                        # fallback: look for '+++ b/...' line
                        m2 = re.search(r"\+\+\+ b/(.+)$", part, re.MULTILINE)
                        if m2:
                            new_path = m2.group(1)

                    # estimate additions/deletions
                    additions = len(re.findall(r'^\+(?!\+\+)', part, re.MULTILINE))
                    deletions = len(re.findall(r'^-(?!--)', part, re.MULTILINE))
                    changes.append({'diff': part, 'new_path': new_path, 'additions': additions, 'deletions': deletions})
                return changes

            # fallback to changes API if .diff not available
            url = f"{self.bitbucket_url}/rest/api/latest/projects/{self.repo_project}/repos/{self.repo_slug}/pull-requests/{self.pull_request_id}/changes?limit=500"
            headers = self._auth_headers()
            # retry a few times due to eventual consistency
            for attempt in range(3):
                r = requests.get(url, headers=headers, timeout=20, verify=False)
                logger.debug(f"Bitbucket get changes status: {r.status_code} for {url}")
                if r.status_code == 200:
                    data = r.json()
                    values = data.get('values', [])
                    for v in values:
                        # convert to common shape
                        path = v.get('path') or {}
                        new_path = path.get('toString') if isinstance(path, dict) else path
                        change = {
                            'diff': v.get('diff') or v.get('content') or '',
                            'new_path': new_path,
                            'additions': v.get('linesAdded', 0) or v.get('additions', 0),
                            'deletions': v.get('linesRemoved', 0) or v.get('deletions', 0)
                        }
                        changes.append(change)
                    return changes
                else:
                    logger.debug(f"Bitbucket changes request failed: {r.status_code}, retrying")
                    time.sleep(2)
        except Exception as e:
            logger.error(f"Failed to get pull request changes from Bitbucket: {str(e)}")
        return changes

    def get_pull_request_commits(self) -> list:
        if not self.pull_request_id or not (self.repo_project and self.repo_slug):
            pr = self.webhook_data.get('pullRequest') or self.webhook_data.get('pull_request') or {}
            return pr.get('commits') or []
        try:
            url = f"{self.bitbucket_url}/rest/api/latest/projects/{self.repo_project}/repos/{self.repo_slug}/pull-requests/{self.pull_request_id}/commits?limit=500"
            headers = self._auth_headers()
            r = requests.get(url, headers=headers, timeout=20, verify=False)
            if r.status_code == 200:
                commits = r.json().get('values', [])
                gitlab_format_commits = []
                for c in commits:
                    gitlab_commit = {
                        'id': c.get('id') or c.get('displayId'),
                        'title': c.get('message', '').split('\n')[0],
                        'message': c.get('message', ''),
                        'author_name': c.get('author', {}).get('name') if isinstance(c.get('author'), dict) else None,
                        'author_email': None,
                        'created_at': c.get('authorTimestamp') if c.get('authorTimestamp') else None,
                        'web_url': None
                    }
                    gitlab_format_commits.append(gitlab_commit)
                return gitlab_format_commits
        except Exception as e:
            logger.error(f"Failed to get pull request commits from Bitbucket: {str(e)}")
        return []

    def add_pull_request_notes(self, review_result):
        if not (self.repo_project and self.repo_slug and self.pull_request_id):
            logger.warn("Missing repo info to add comment")
            return
        try:
            url = f"{self.bitbucket_url}/rest/api/latest/projects/{self.repo_project}/repos/{self.repo_slug}/pull-requests/{self.pull_request_id}/comments"
            headers = self._auth_headers()
            data = {'text': review_result}
            r = requests.post(url, headers=headers, json=data, timeout=20, verify=False)
            logger.debug(f"Add comment to Bitbucket PR {url}: {r.status_code}, {r.text[:200]}")
            if r.status_code in (200, 201):
                logger.info("Comment successfully added to pull request.")
            else:
                logger.error(f"Failed to add comment: {r.status_code}, {r.text[:500]}")
        except Exception as e:
            logger.error(f"Failed to add PR comment: {str(e)}")

    def target_branch_protected(self) -> bool:
        # Bitbucket Server branch permission API is not handled; default to False
        return False


class PushHandler:
    def __init__(self, webhook_data: dict, bitbucket_token: str, bitbucket_url: str):
        self.webhook_data = webhook_data
        self.bitbucket_token = bitbucket_token
        self.bitbucket_url = bitbucket_url.rstrip('/')
        self.event_type = 'push'
        self.repo_project = None
        self.repo_slug = None
        self.branch_name = None
        self.commit_list = []
        self.parse_push_event()

    def parse_push_event(self):
        repository = self.webhook_data.get('repository', {})
        project = repository.get('project') or {}
        self.repo_project = project.get('key') or repository.get('projectKey')
        self.repo_slug = repository.get('slug') or repository.get('name')
        # bitbucket payload shape: push.changes -> list
        push = self.webhook_data.get('push') or {}
        changes = push.get('changes') if isinstance(push, dict) else self.webhook_data.get('changes')
        if isinstance(changes, list) and len(changes) > 0:
            ref = changes[0].get('ref') or {}
            self.branch_name = ref.get('displayId') or ref.get('id', '')
            # normalize
            if isinstance(self.branch_name, str) and self.branch_name.startswith('refs/heads/'):
                self.branch_name = self.branch_name.replace('refs/heads/', '')
            # collect commits
            commits = []
            for c in changes[0].get('commits', []) if changes[0].get('commits') else []:
                commit_info = {
                    'message': c.get('message', ''),
                    'author': c.get('author', {}).get('name') if isinstance(c.get('author'), dict) else None,
                    'timestamp': c.get('authorTimestamp') if c.get('authorTimestamp') else None,
                    'url': None
                }
                commits.append(commit_info)
            self.commit_list = commits
        else:
            # fallback: top-level commits
            self.commit_list = self.webhook_data.get('commits', [])
            self.branch_name = self.webhook_data.get('ref', '').replace('refs/heads/', '') if self.webhook_data.get('ref') else None

    def get_push_commits(self) -> list:
        return [{'message': c.get('message'), 'author': c.get('author'), 'timestamp': c.get('timestamp'), 'url': c.get('url')} for c in self.commit_list]

    def get_push_changes(self) -> list:
        # Attempt to fetch changes via REST API for each commit if we have project/repo info
        changes = []
        if not (self.repo_project and self.repo_slug) or not self.commit_list:
            return changes
        headers = {'Authorization': f'Bearer {self.bitbucket_token}'}
        for c in self.commit_list:
            cid = c.get('id') or c.get('hash')
            if not cid:
                continue
            try:
                url = f"{self.bitbucket_url}/rest/api/latest/projects/{self.repo_project}/repos/{self.repo_slug}/commits/{cid}/changes"
                r = requests.get(url, headers=headers, timeout=20, verify=False)
                if r.status_code == 200:
                    data = r.json()
                    for v in data.get('values', []):
                        change = {
                            'diff': v.get('diff') or '',
                            'new_path': v.get('path', {}).get('toString') if isinstance(v.get('path'), dict) else v.get('path'),
                            'additions': v.get('linesAdded', 0),
                            'deletions': v.get('linesRemoved', 0)
                        }
                        changes.append(change)
            except Exception as e:
                logger.debug(f"Failed to fetch commit changes {cid}: {str(e)}")
        return changes

    def add_push_notes(self, message: str):
        # Bitbucket Server doesn't support commit comments in uniform fashion here; skip or post to PR if available
        logger.info('add_push_notes: Bitbucket push notes posting is not implemented; skipping')
