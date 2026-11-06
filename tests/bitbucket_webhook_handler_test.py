"""
Bitbucket Server / Data Center webhook 处理逻辑的单元测试。

运行方式：``python3 -m unittest discover -s tests -p "bitbucket*_test.py" -v``
或直接执行 ``python3 tests/bitbucket_webhook_handler_test.py``
"""

import hashlib
import hmac
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.bitbucket.webhook_handler import (  # noqa: E402
    BitbucketClient,
    PullRequestHandler,
    PushHandler,
    filter_changes,
    parse_unified_diff,
    path_to_string,
    rest_diff_to_unified,
    verify_webhook_signature,
)

RAW_DIFF = """diff --git a/src/main/java/Demo.java b/src/main/java/Demo.java
index 1234567..89abcde 100644
--- a/src/main/java/Demo.java
+++ b/src/main/java/Demo.java
@@ -1,4 +1,6 @@
 public class Demo {
-    int value = 1;
+    int value = 2;
+    int other = 3;
 }
diff --git a/README.md b/README.md
index 1111111..2222222 100644
--- a/README.md
+++ b/README.md
@@ -1 +1,2 @@
 # Title
+new line
diff --git a/src/removed/Old.java b/src/removed/Old.java
deleted file mode 100644
--- a/src/removed/Old.java
+++ /dev/null
@@ -1,2 +0,0 @@
-public class Old {
-}
"""

PR_PAYLOAD = {
    "eventKey": "pr:opened",
    "date": "2024-05-06T10:00:00+0800",
    "actor": {
        "name": "admin",
        "emailAddress": "admin@example.com",
        "displayName": "Administrator",
        "id": 2,
        "slug": "admin",
    },
    "pullRequest": {
        "id": 11,
        "version": 0,
        "title": "a new file added",
        "state": "OPEN",
        "open": True,
        "closed": False,
        "fromRef": {
            "id": "refs/heads/feature",
            "displayId": "feature",
            "latestCommit": "ef8755f06ee4b28c96a847a95cb8ec8ed6ddd1ca",
            "repository": {
                "slug": "demo",
                "name": "demo",
                "scmId": "git",
                "project": {"key": "PROJ", "id": 84, "name": "project"},
            },
        },
        "toRef": {
            "id": "refs/heads/master",
            "displayId": "master",
            "latestCommit": "178864a7d521b6f5e720b386b2c2b0ef8563e0dc",
            "repository": {
                "slug": "demo",
                "name": "demo",
                "scmId": "git",
                "project": {"key": "PROJ", "id": 84, "name": "project"},
            },
        },
        "author": {
            "user": {
                "name": "admin",
                "emailAddress": "admin@example.com",
                "displayName": "Administrator",
                "slug": "admin",
            },
            "role": "AUTHOR",
        },
        "links": {"self": [{"href": "http://bitbucket.example.com/projects/PROJ/repos/demo/pull-requests/11"}]},
    },
}

PUSH_PAYLOAD = {
    "eventKey": "repo:refs_changed",
    "date": "2024-05-06T10:00:00+0800",
    "actor": {
        "name": "admin",
        "emailAddress": "admin@example.com",
        "displayName": "Administrator",
        "id": 2,
        "slug": "admin",
    },
    "repository": {
        "slug": "demo",
        "id": 1,
        "name": "demo",
        "scmId": "git",
        "project": {"key": "PROJ", "id": 1, "name": "Project 1"},
    },
    "changes": [
        {
            "ref": {"id": "refs/heads/master", "displayId": "master", "type": "BRANCH"},
            "refId": "refs/heads/master",
            "fromHash": "197a3e0d2f9a2b3ed1c4fe5923d5dd701bee9fdd",
            "toHash": "a00945762949b7b787ecabc388c0e20b1b85f0b4",
            "type": "UPDATE",
        }
    ],
    "commits": [
        {
            "id": "a00945762949b7b787ecabc388c0e20b1b85f0b4",
            "displayId": "a0094576294",
            "author": {"name": "Administrator", "emailAddress": "admin@example.com"},
            "authorTimestamp": 1673403328000,
            "message": "My commit message",
            "parents": [{"id": "197a3e0d2f9a2b3ed1c4fe5923d5dd701bee9fdd", "displayId": "197a3e0d"}],
        }
    ],
}


class TestParseUnifiedDiff(unittest.TestCase):

    def test_parse_files_and_counts(self):
        changes = parse_unified_diff(RAW_DIFF)
        paths = [c['new_path'] for c in changes]
        # 被删除的文件（+++ /dev/null）也要保留，否则模型不知道这次删了东西
        self.assertEqual(paths,
                         ['src/main/java/Demo.java', 'README.md', 'src/removed/Old.java'])

    def test_additions_deletions(self):
        changes = {c['new_path']: c for c in parse_unified_diff(RAW_DIFF)}
        java = changes['src/main/java/Demo.java']
        self.assertEqual(java['additions'], 2)
        self.assertEqual(java['deletions'], 1)
        # diff 内容只保留 hunk 部分（与 GitLab 的 changes[].diff 结构保持一致）
        self.assertTrue(java['diff'].startswith('@@ -1,4 +1,6 @@'))
        self.assertIn('+    int other = 3;', java['diff'])

    def test_empty_input(self):
        self.assertEqual(parse_unified_diff(''), [])
        self.assertEqual(parse_unified_diff(None), [])

    def test_parse_diff_without_git_header(self):
        raw = """--- a/src/App.java
+++ b/src/App.java
@@ -1,2 +1,3 @@
 int a = 1;
+int b = 2;
"""
        changes = parse_unified_diff(raw)
        self.assertEqual(len(changes), 1)
        self.assertEqual(changes[0]['new_path'], 'src/App.java')
        self.assertEqual(changes[0]['additions'], 1)

    def test_parse_bitbucket_src_dst_prefix(self):
        """Bitbucket DC 的 diff 使用 src:// / dst:// 前缀，不是 git 的 a/ b/。"""
        raw = """diff --git src://Sources/ASW/App.c dst://Sources/ASW/App.c
index 8c10770..b5a6d27 100644
--- src://Sources/ASW/App.c
+++ dst://Sources/ASW/App.c
@@ -30,3 +30,4 @@
 int a = 1;
-int b = 2;
+int b = 3;
+int c = 4;
"""
        changes = parse_unified_diff(raw)
        self.assertEqual(len(changes), 1)
        self.assertEqual(changes[0]['new_path'], 'Sources/ASW/App.c')
        self.assertEqual(changes[0]['old_path'], 'Sources/ASW/App.c')
        self.assertEqual(changes[0]['additions'], 2)
        self.assertEqual(changes[0]['deletions'], 1)

    def test_parse_bitbucket_deleted_file(self):
        """删除的文件要保留成 DELETE 条目，模型必须知道有文件被删了。"""
        raw = """diff --git src://old.txt dst://old.txt
deleted file mode 100644
--- src://old.txt
+++ /dev/null
@@ -1,2 +0,0 @@
-gone
-text
"""
        changes = parse_unified_diff(raw)
        self.assertEqual(len(changes), 1)
        self.assertEqual(changes[0]['type'], 'DELETE')
        self.assertEqual(changes[0]['new_path'], 'old.txt')
        self.assertEqual(changes[0]['deletions'], 2)
        self.assertEqual(changes[0]['additions'], 0)
        # 被删掉的内容必须保留
        self.assertIn('-gone', changes[0]['diff'])

    def test_parse_bitbucket_binary_file_is_kept(self):
        """二进制文件没有 --- / +++ 行，要保留成 BINARY 条目，且不影响后续文件。"""
        raw = """diff --git src://logo.png dst://logo.png
index 1111111..2222222 100644
Binary files src://logo.png and dst://logo.png differ
diff --git src://App.c dst://App.c
index 3333333..4444444 100644
--- src://App.c
+++ dst://App.c
@@ -1,2 +1,3 @@
 int a = 1;
+int b = 2;
"""
        changes = parse_unified_diff(raw)
        self.assertEqual(len(changes), 2)
        self.assertEqual(changes[0]['type'], 'BINARY')
        self.assertEqual(changes[0]['new_path'], 'logo.png')
        self.assertEqual(changes[0]['additions'], 0)
        self.assertEqual(changes[0]['deletions'], 0)
        # 后续文件不受影响
        self.assertEqual(changes[1]['type'], 'MODIFY')
        self.assertEqual(changes[1]['new_path'], 'App.c')


class TestFilterChanges(unittest.TestCase):

    def setUp(self):
        os.environ['SUPPORTED_EXTENSIONS'] = '.java,.md'

    def test_filters_by_extension_and_keeps_shape(self):
        changes = parse_unified_diff(RAW_DIFF)
        filtered = filter_changes(changes)
        # 删除的文件也在其中：模型需要知道它被删了
        self.assertEqual(sorted(c['new_path'] for c in filtered),
                         ['README.md', 'src/main/java/Demo.java', 'src/removed/Old.java'])
        for item in filtered:
            # 四个必备字段 + 可选的 type / old_path
            self.assertTrue({'diff', 'new_path', 'additions', 'deletions'}.issubset(item.keys()))
            self.assertLessEqual(item.keys(),
                                 {'diff', 'new_path', 'additions', 'deletions', 'type', 'old_path'})

    def test_counts_derived_from_diff_when_missing(self):
        filtered = filter_changes([
            {'new_path': 'a.java', 'diff': '+line1\n+line2\n-old\n', 'additions': 0, 'deletions': 0}
        ])
        self.assertEqual(filtered[0]['additions'], 2)
        self.assertEqual(filtered[0]['deletions'], 1)

    def test_skips_empty_path(self):
        self.assertEqual(filter_changes([{'new_path': '', 'diff': '+x'}]), [])


class TestPathHelpers(unittest.TestCase):

    def test_path_to_string_prefers_components(self):
        self.assertEqual(
            path_to_string({'components': ['src', 'main', 'Demo.java'], 'name': 'Demo.java'}),
            'src/main/Demo.java')

    def test_path_to_string_fallback_to_parent_and_name(self):
        self.assertEqual(path_to_string({'parent': 'src/main', 'name': 'Demo.java'}), 'src/main/Demo.java')

    def test_path_to_string_empty(self):
        self.assertEqual(path_to_string({}), '')


class TestRestDiffToUnified(unittest.TestCase):

    def test_converts_segments_to_unified(self):
        diff_data = {
            "hunks": [{
                "sourceLine": 1,
                "sourceSpan": 3,
                "destinationLine": 1,
                "destinationSpan": 3,
                "segments": [
                    {"type": "CONTEXT", "lines": [{"source": 1, "destination": 1, "line": "keep"}]},
                    {"type": "REMOVED", "lines": [{"source": 2, "line": "old"}]},
                    {"type": "ADDED", "lines": [{"destination": 2, "line": "new"}]},
                ],
            }]
        }
        unified = rest_diff_to_unified(diff_data)
        self.assertIn("@@ -1,3 +1,3 @@", unified)
        self.assertIn(" keep", unified)
        self.assertIn("-old", unified)
        self.assertIn("+new", unified)

    def test_empty(self):
        self.assertEqual(rest_diff_to_unified({}), '')


class TestPullRequestHandler(unittest.TestCase):

    def setUp(self):
        self.handler = PullRequestHandler(PR_PAYLOAD, 'token', 'http://bitbucket.example.com')

    def test_parses_event_and_refs(self):
        self.assertEqual(self.handler.event_type, 'pr:opened')
        self.assertEqual(self.handler.action, 'opened')
        self.assertEqual(self.handler.pull_request_id, 11)
        self.assertEqual(self.handler.project_key, 'PROJ')
        self.assertEqual(self.handler.repository_slug, 'demo')
        self.assertEqual(self.handler.repo_full_name, 'PROJ/demo')
        self.assertEqual(self.handler.source_branch, 'feature')
        self.assertEqual(self.handler.target_branch, 'master')
        self.assertEqual(self.handler.author, 'admin')

    def test_api_path(self):
        self.assertEqual(
            self.handler.pr_api_path,
            '/projects/PROJ/repos/demo/pull-requests/11')

    def test_supported_event_only(self):
        handler = PullRequestHandler({'eventKey': 'pr:merged', 'pullRequest': PR_PAYLOAD['pullRequest']},
                                     'token', 'http://bitbucket.example.com')
        self.assertEqual(handler.get_pull_request_commits(), [])


class TestPushHandler(unittest.TestCase):

    def setUp(self):
        self.handler = PushHandler(PUSH_PAYLOAD, 'token', 'http://bitbucket.example.com')

    def test_parses_repository_and_branch(self):
        self.assertEqual(self.handler.event_type, 'repo:refs_changed')
        self.assertEqual(self.handler.project_key, 'PROJ')
        self.assertEqual(self.handler.repository_slug, 'demo')
        self.assertEqual(self.handler.repo_full_name, 'PROJ/demo')
        self.assertEqual(self.handler.branch_name, 'master')

    def test_get_push_commits_shape(self):
        commits = self.handler.get_push_commits()
        self.assertEqual(len(commits), 1)
        self.assertEqual(commits[0]['message'], 'My commit message')
        self.assertEqual(commits[0]['author'], 'Administrator')
        self.assertIn('/projects/PROJ/repos/demo/commits/', commits[0]['url'])

    def test_tag_push_is_ignored(self):
        payload = dict(PUSH_PAYLOAD)
        payload['changes'] = [{'ref': {'id': 'refs/tags/v1', 'displayId': 'v1', 'type': 'TAG'}}]
        handler = PushHandler(payload, 'token', 'http://bitbucket.example.com')
        self.assertEqual(handler.branch_name, '')


class FakeResponse:
    """模拟 requests.Response 的最小实现。"""

    def __init__(self, status_code=200, text='', json_data=None):
        self.status_code = status_code
        self.text = text
        self._json_data = json_data

    def json(self):
        if self._json_data is None:
            raise ValueError('no json')
        return self._json_data


class TestWebhookSignature(unittest.TestCase):

    def test_official_bitbucket_test_vector(self):
        """Atlassian 官方文档给出的测试向量。"""
        secret = "It's a Secret to Everybody"
        payload = b'Hello World!'
        expected = 'sha256=a4771c39fbe90f317c7824e83ddef3caae9cb3d976c214ace1f2937e133263c9'
        self.assertTrue(verify_webhook_signature(payload, expected, secret))

    def test_valid_signature(self):
        secret = 'top-secret'
        payload = b'{"eventKey": "pr:opened"}'
        signature = 'sha256=' + hmac.new(secret.encode('utf-8'), payload,
                                         hashlib.sha256).hexdigest()
        self.assertTrue(verify_webhook_signature(payload, signature, secret))

    def test_invalid_signature(self):
        secret = 'top-secret'
        payload = b'{"eventKey": "pr:opened"}'
        self.assertFalse(verify_webhook_signature(payload, 'sha256=' + '0' * 64, secret))

    def test_wrong_secret(self):
        secret = 'top-secret'
        payload = b'{"eventKey": "pr:opened"}'
        signature = 'sha256=' + hmac.new('other-secret'.encode('utf-8'), payload,
                                         hashlib.sha256).hexdigest()
        self.assertFalse(verify_webhook_signature(payload, signature, secret))

    def test_missing_signature(self):
        self.assertFalse(verify_webhook_signature(b'{}', '', 'top-secret'))

    def test_missing_secret(self):
        payload = b'{}'
        signature = 'sha256=' + hmac.new(b'x', payload, hashlib.sha256).hexdigest()
        self.assertFalse(verify_webhook_signature(payload, signature, ''))

    def test_empty_body(self):
        self.assertFalse(verify_webhook_signature(b'', 'sha256=abc', 'top-secret'))

    def test_unsupported_algorithm(self):
        self.assertFalse(verify_webhook_signature(b'{}', 'md5=abc', 'top-secret'))

    def test_algorithm_prefix_is_case_insensitive(self):
        secret = 'top-secret'
        payload = b'{}'
        digest = hmac.new(secret.encode('utf-8'), payload, hashlib.sha256).hexdigest()
        self.assertTrue(verify_webhook_signature(payload, 'SHA256=' + digest, secret))

    def test_signature_must_match_raw_bytes(self):
        """签名是对原始字节算的，格式化过的 JSON 对不上。"""
        secret = 'top-secret'
        raw = b'{"eventKey": "pr:opened",  "id": 25}'
        pretty = b'{\n  "eventKey": "pr:opened",\n  "id": 25\n}'
        signature = 'sha256=' + hmac.new(secret.encode('utf-8'), raw, hashlib.sha256).hexdigest()
        self.assertTrue(verify_webhook_signature(raw, signature, secret))
        self.assertFalse(verify_webhook_signature(pretty, signature, secret))

    def test_sha1_supported(self):
        secret = 'top-secret'
        payload = b'{}'
        signature = 'sha1=' + hmac.new(secret.encode('utf-8'), payload, hashlib.sha1).hexdigest()
        self.assertTrue(verify_webhook_signature(payload, signature, secret))


class TestBitbucketClient(unittest.TestCase):

    def test_bearer_auth_header(self):
        """只使用 Bearer 认证，不提供任何 Basic 认证能力。"""
        client = BitbucketClient('http://bb.example.com', 'secret')
        self.assertEqual(client.headers['Authorization'], 'Bearer secret')
        self.assertNotIn('Basic', client.headers['Authorization'])

    def test_client_has_no_username(self):
        """客户端不再接受也不需要用户名。"""
        client = BitbucketClient('http://bb.example.com', 'secret')
        self.assertFalse(hasattr(client, 'username'))

    def test_api_url_uses_rest_api_1_0(self):
        client = BitbucketClient('http://bb.example.com', 'secret')
        self.assertEqual(client.api_url('/projects/PROJ/repos/demo'),
                         'http://bb.example.com/rest/api/1.0/projects/PROJ/repos/demo')

    def test_plugin_url(self):
        client = BitbucketClient('http://bb.example.com', 'secret')
        self.assertEqual(client.plugin_url('branch-permissions', '/x'),
                         'http://bb.example.com/rest/branch-permissions/latest/x')

    def test_get_text_returns_none_on_error(self):
        client = BitbucketClient('http://bb.example.com', 'secret')
        with mock.patch('src.bitbucket.webhook_handler.requests.get',
                        return_value=FakeResponse(404, 'not found')):
            self.assertIsNone(client.get_text('/x'))

    def test_get_text_returns_empty_string_on_empty_body(self):
        client = BitbucketClient('http://bb.example.com', 'secret')
        with mock.patch('src.bitbucket.webhook_handler.requests.get',
                        return_value=FakeResponse(200, '')):
            self.assertEqual(client.get_text('/x'), '')

    def test_get_text_requests_text_plain(self):
        """Bitbucket 的 .diff 接口只接受 text/plain，用 application/json 会返回 406。"""
        client = BitbucketClient('http://bb.example.com', 'secret')
        with mock.patch('src.bitbucket.webhook_handler.requests.get',
                        return_value=FakeResponse(200, 'diff')) as get:
            client.get_text('/x')
        headers = get.call_args[1]['headers']
        self.assertEqual(headers['Accept'], 'text/plain')

    def test_get_json_requests_application_json(self):
        client = BitbucketClient('http://bb.example.com', 'secret')
        with mock.patch('src.bitbucket.webhook_handler.requests.get',
                        return_value=FakeResponse(200, '{}', {})) as get:
            client.get_json('/x')
        headers = get.call_args[1]['headers']
        self.assertEqual(headers['Accept'], 'application/json')

    def test_get_sends_bearer_only(self):
        """GET 只发一次请求，且使用 Bearer。"""
        client = BitbucketClient('http://bb.example.com', 'secret')
        with mock.patch('src.bitbucket.webhook_handler.requests.get',
                        return_value=FakeResponse(200, '{}', {})) as get:
            result = client.get('/x')
        self.assertEqual(result.status_code, 200)
        self.assertEqual(get.call_count, 1)
        self.assertTrue(get.call_args_list[0][1]['headers']['Authorization'].startswith('Bearer '))

    def test_post_sends_bearer_only(self):
        """POST 只发一次请求，且使用 Bearer。"""
        client = BitbucketClient('http://bb.example.com', 'secret')
        with mock.patch('src.bitbucket.webhook_handler.requests.post',
                        return_value=FakeResponse(201, '{}', {})) as post:
            result = client.post('/x', {'text': 'hi'})
        self.assertEqual(result.status_code, 201)
        self.assertEqual(post.call_count, 1)
        self.assertTrue(post.call_args_list[0][1]['headers']['Authorization'].startswith('Bearer '))

    def test_no_basic_retry_on_401(self):
        """401 不再回退到 Basic，直接把 401 返回给调用方。"""
        client = BitbucketClient('http://bb.example.com', 'secret')
        with mock.patch('src.bitbucket.webhook_handler.requests.get',
                        return_value=FakeResponse(401, 'unauthorized')) as get:
            result = client.get('/x')
        self.assertEqual(result.status_code, 401)
        self.assertEqual(get.call_count, 1)

    def test_no_basic_retry_on_401_for_post(self):
        client = BitbucketClient('http://bb.example.com', 'secret')
        with mock.patch('src.bitbucket.webhook_handler.requests.post',
                        return_value=FakeResponse(401, 'unauthorized')) as post:
            result = client.post('/x', {'text': 'hi'})
        self.assertEqual(result.status_code, 401)
        self.assertEqual(post.call_count, 1)


class TestPullRequestApiCalls(unittest.TestCase):

    def setUp(self):
        self.handler = PullRequestHandler(PR_PAYLOAD, 'token', 'http://bb.example.com')

    @mock.patch('src.bitbucket.webhook_handler.time.sleep', return_value=None)
    def test_get_pull_request_changes_uses_raw_diff_endpoint(self, _sleep):
        with mock.patch.object(self.handler.client, 'get_text', return_value=RAW_DIFF) as get_text:
            changes = self.handler.get_pull_request_changes()
        get_text.assert_called_once_with('/projects/PROJ/repos/demo/pull-requests/11.diff')
        self.assertEqual(len(changes), 3)

    @mock.patch('src.bitbucket.webhook_handler.time.sleep', return_value=None)
    def test_get_pull_request_changes_retries_on_failure(self, _sleep):
        with mock.patch.object(self.handler.client, 'get_text', side_effect=[None, None, RAW_DIFF]) as get_text:
            changes = self.handler.get_pull_request_changes()
        self.assertEqual(get_text.call_count, 3)
        self.assertEqual(len(changes), 3)

    @mock.patch('src.bitbucket.webhook_handler.time.sleep', return_value=None)
    def test_empty_diff_does_not_retry(self, _sleep):
        with mock.patch.object(self.handler.client, 'get_text', return_value='') as get_text:
            changes = self.handler.get_pull_request_changes()
        get_text.assert_called_once()
        self.assertEqual(changes, [])

    def test_get_pull_request_commits_pagination(self):
        page1 = {'values': [{'id': 'aaa', 'message': 'first commit',
                             'author': {'name': 'admin', 'emailAddress': 'a@b.c'},
                             'authorTimestamp': 1}],
                 'isLastPage': False, 'nextPageStart': 1}
        page2 = {'values': [{'id': 'bbb', 'message': 'second commit', 'author': {}}],
                 'isLastPage': True}
        with mock.patch.object(self.handler.client, 'get_json', side_effect=[page1, page2]):
            commits = self.handler.get_pull_request_commits()
        self.assertEqual(len(commits), 2)
        self.assertEqual(commits[0]['title'], 'first commit')
        self.assertEqual(commits[0]['author_name'], 'admin')
        self.assertEqual(commits[1]['id'], 'bbb')

    def test_add_pull_request_notes_posts_comment(self):
        with mock.patch.object(self.handler.client, 'post',
                               return_value=FakeResponse(201, '{}')) as post:
            self.handler.add_pull_request_notes('review result')
        post.assert_called_once_with('/projects/PROJ/repos/demo/pull-requests/11/comments',
                                     {'text': 'review result'})

    def test_target_branch_protected_matches_restriction(self):
        restrictions = {'values': [{'matcher': {'displayId': 'mas*', 'id': 'refs/heads/mas*',
                                                'type': {'id': 'BRANCH'}}}]}
        with mock.patch.object(self.handler.client, 'get_json', return_value=restrictions):
            self.assertTrue(self.handler.target_branch_protected())

    def test_target_branch_not_protected(self):
        restrictions = {'values': [{'matcher': {'displayId': 'release/*', 'id': 'refs/heads/release/*',
                                                'type': {'id': 'BRANCH'}}}]}
        with mock.patch.object(self.handler.client, 'get_json', return_value=restrictions):
            self.assertFalse(self.handler.target_branch_protected())


class TestPushApiCalls(unittest.TestCase):

    def setUp(self):
        self.handler = PushHandler(PUSH_PAYLOAD, 'token', 'http://bb.example.com')

    def test_get_push_changes_uses_repo_range_diff(self):
        with mock.patch.object(self.handler.client, 'get_text', return_value=RAW_DIFF) as get_text:
            changes = self.handler.get_push_changes()
        args, kwargs = get_text.call_args
        self.assertEqual(args[0], '/projects/PROJ/repos/demo/diff')
        self.assertEqual(kwargs['params']['since'], '197a3e0d2f9a2b3ed1c4fe5923d5dd701bee9fdd')
        self.assertEqual(kwargs['params']['until'], 'a00945762949b7b787ecabc388c0e20b1b85f0b4')
        self.assertEqual(len(changes), 3)

    def test_get_push_changes_falls_back_to_commit_diff(self):
        with mock.patch.object(self.handler.client, 'get_text', side_effect=['', RAW_DIFF]):
            changes = self.handler.get_push_changes()
        self.assertEqual(len(changes), 3)

    def test_add_push_notes_comments_last_commit(self):
        with mock.patch.object(self.handler.client, 'post',
                               return_value=FakeResponse(201, '{}')) as post:
            self.handler.add_push_notes('push review')
        post.assert_called_once_with(
            '/projects/PROJ/repos/demo/commits/a00945762949b7b787ecabc388c0e20b1b85f0b4/comments',
            {'text': 'push review'})


if __name__ == '__main__':
    unittest.main(verbosity=2)