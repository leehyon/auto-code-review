"""
Bitbucket 事件在队列 worker 中的集成测试。

只验证 worker 的编排逻辑（过滤条件、事件分发、实体字段），不调用真实 API 与 LLM。

运行方式：``python3 -m unittest discover -s tests -p "bitbucket_worker_test.py" -v``
"""

import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

PR_PAYLOAD = {
    "eventKey": "pr:opened",
    "actor": {"name": "admin", "displayName": "Administrator", "slug": "admin"},
    "pullRequest": {
        "id": 11,
        "title": "a new file added",
        "state": "OPEN",
        "fromRef": {
            "id": "refs/heads/feature",
            "displayId": "feature",
            "latestCommit": "ef8755f06ee4b28c96a847a95cb8ec8ed6ddd1ca",
            "repository": {
                "slug": "demo", "name": "demo", "scmId": "git",
                "project": {"key": "PROJ", "id": 84, "name": "project"},
            },
        },
        "toRef": {
            "id": "refs/heads/master",
            "displayId": "master",
            "latestCommit": "178864a7d521b6f5e720b386b2c2b0ef8563e0dc",
            "repository": {
                "slug": "demo", "name": "demo", "scmId": "git",
                "project": {"key": "PROJ", "id": 84, "name": "project"},
            },
        },
        "author": {
            "user": {"name": "admin", "displayName": "Administrator", "slug": "admin"},
            "role": "AUTHOR",
        },
        "links": {"self": [{"href": "http://bb.example.com/projects/PROJ/repos/demo/pull-requests/11"}]},
    },
}

PUSH_PAYLOAD = {
    "eventKey": "repo:refs_changed",
    "actor": {"name": "admin", "displayName": "Administrator", "slug": "admin"},
    "repository": {
        "slug": "demo", "id": 1, "name": "demo", "scmId": "git",
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
"""

COMMITS = [
    {"id": "aaa", "title": "first commit", "message": "first commit",
     "author_name": "admin", "author_email": "a@b.c", "created_at": 1, "web_url": "http://x"},
]


def _reset_env():
    os.environ['SUPPORTED_EXTENSIONS'] = '.java'
    os.environ['PUSH_REVIEW_ENABLED'] = '1'
    os.environ['MERGE_REVIEW_ONLY_PROTECTED_BRANCHES_ENABLED'] = '0'
    os.environ['MERGE_REVIEW_ONLY_BRANCH_NAME'] = ''


class TestBitbucketWorker(unittest.TestCase):

    def setUp(self):
        _reset_env()
        # 避免 worker 真正写数据库 / 发 IM 通知
        self.db_patcher = mock.patch('src.event.event_manager.ReviewService.insert_mr_review_log')
        self.db_patcher2 = mock.patch('src.event.event_manager.ReviewService.insert_push_review_log')
        self.notify_patcher = mock.patch('src.utils.messaging.notifier.send_notification')
        self.db_patcher.start()
        self.db_patcher2.start()
        self.notify_patcher.start()
        self.addCleanup(self.db_patcher.stop)
        self.addCleanup(self.db_patcher2.stop)
        self.addCleanup(self.notify_patcher.stop)

    def _run_pr(self, payload=None, protected=False):
        from src.queue import worker
        payload = payload or PR_PAYLOAD
        sent = []

        def fake_review(changes, commits_text, changes_data, repo_context=''):
            return 'REVIEW RESULT score 88'

        with mock.patch.object(worker, 'CodeReviewer') as reviewer, \
                mock.patch.object(worker.CodeReviewer, 'parse_review_score', return_value=88), \
                mock.patch.object(worker, 'event_manager') as event_manager, \
                mock.patch.object(worker.BitbucketPullRequestHandler, 'get_pull_request_changes',
                                  return_value=[{'diff': '@@ -1 +1 @@\n+int x = 1;',
                                                 'new_path': 'src/main/java/Demo.java',
                                                 'additions': 1, 'deletions': 0}]), \
                mock.patch.object(worker.BitbucketPullRequestHandler, 'get_pull_request_commits',
                                  return_value=COMMITS), \
                mock.patch.object(worker.BitbucketPullRequestHandler, 'add_pull_request_notes') as notes, \
                mock.patch.object(worker.BitbucketPullRequestHandler, 'target_branch_protected',
                                  return_value=protected):
            reviewer.return_value.review_and_strip_code.side_effect = fake_review
            event_manager.__getitem__.return_value.send.side_effect = lambda entity: sent.append(entity)
            worker.handle_bitbucket_pull_request_event(payload, 'token', 'http://bb.example.com', 'bb_example_com')
            return sent, notes

    def test_pr_event_dispatches_entity(self):
        sent, notes = self._run_pr()
        self.assertEqual(len(sent), 1)
        entity = sent[0]
        self.assertEqual(entity.project_name, 'demo')
        self.assertEqual(entity.author, 'admin')
        self.assertEqual(entity.source_branch, 'feature')
        self.assertEqual(entity.target_branch, 'master')
        self.assertEqual(entity.url, 'http://bb.example.com/projects/PROJ/repos/demo/pull-requests/11')
        self.assertEqual(entity.score, 88)
        self.assertEqual(entity.additions, 1)
        self.assertEqual(entity.deletions, 0)
        notes.assert_called_once()

    def test_pr_ignored_when_target_branch_filtered(self):
        os.environ['MERGE_REVIEW_ONLY_BRANCH_NAME'] = 'release'
        sent, notes = self._run_pr()
        self.assertEqual(len(sent), 0)
        notes.assert_not_called()

    def test_pr_ignored_when_target_branch_protected_only(self):
        os.environ['MERGE_REVIEW_ONLY_PROTECTED_BRANCHES_ENABLED'] = '1'
        sent, notes = self._run_pr(protected=False)
        self.assertEqual(len(sent), 0)
        notes.assert_not_called()

    def test_pr_ignored_for_unsupported_action(self):
        payload = dict(PR_PAYLOAD)
        payload['eventKey'] = 'pr:modified'
        sent, notes = self._run_pr(payload=payload)
        self.assertEqual(len(sent), 0)

    def test_push_event_dispatches_entity(self):
        from src.queue import worker
        sent = []

        with mock.patch.object(worker, 'CodeReviewer') as reviewer, \
                mock.patch.object(worker.CodeReviewer, 'parse_review_score', return_value=77), \
                mock.patch.object(worker, 'event_manager') as event_manager, \
                mock.patch.object(worker.BitbucketPushHandler, 'get_push_changes',
                                  return_value=[{'diff': '@@ -1 +1 @@\n+int x = 1;',
                                                 'new_path': 'src/main/java/Demo.java',
                                                 'additions': 1, 'deletions': 0}]), \
                mock.patch.object(worker.BitbucketPushHandler, 'add_push_notes') as notes:
            reviewer.return_value.review_and_strip_code.return_value = 'REVIEW RESULT'
            event_manager.__getitem__.return_value.send.side_effect = lambda entity: sent.append(entity)
            worker.handle_bitbucket_push_event(PUSH_PAYLOAD, 'token', 'http://bb.example.com', 'bb_example_com')

        self.assertEqual(len(sent), 1)
        entity = sent[0]
        self.assertEqual(entity.project_name, 'demo')
        self.assertEqual(entity.author, 'admin')
        self.assertEqual(entity.branch, 'master')
        self.assertEqual(entity.score, 77)
        self.assertEqual(entity.additions, 1)
        notes.assert_called_once()

    def test_push_not_reviewed_when_disabled(self):
        from src.queue import worker
        os.environ['PUSH_REVIEW_ENABLED'] = '0'
        sent = []

        with mock.patch.object(worker, 'CodeReviewer') as reviewer, \
                mock.patch.object(worker, 'event_manager') as event_manager, \
                mock.patch.object(worker.BitbucketPushHandler, 'get_push_changes') as get_changes, \
                mock.patch.object(worker.BitbucketPushHandler, 'add_push_notes') as notes:
            event_manager.__getitem__.return_value.send.side_effect = lambda entity: sent.append(entity)
            worker.handle_bitbucket_push_event(PUSH_PAYLOAD, 'token', 'http://bb.example.com', 'bb_example_com')

        get_changes.assert_not_called()
        notes.assert_not_called()
        self.assertEqual(len(sent), 1)
        self.assertIsNone(sent[0].review_result)


if __name__ == '__main__':
    unittest.main(verbosity=2)
