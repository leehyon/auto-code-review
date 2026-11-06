"""审查输入保真度与覆盖度披露测试。

覆盖四件事：
1. diff 解析不再丢弃删除/重命名/二进制文件
2. 覆盖度统计能区分「送进模型」和「被预算挤掉」的文件
3. 覆盖度同时出现在 prompt 和 PR 评论里
4. 仓库级指令文件有界读取，且读不到不影响审查
"""
import os
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.bitbucket.webhook_handler import parse_unified_diff
from src.utils.code_reviewer import CodeReviewer


DELETE_DIFF = """diff --git src://a/gone.c dst://a/gone.c
deleted file mode 100644
--- src://a/gone.c
+++ /dev/null
@@ -1,3 +0,0 @@
-int a;
-int b;
-int c;
"""

RENAME_DIFF = """diff --git src://a/old.c dst://a/new.c
similarity index 100%
rename from a/old.c
rename to a/new.c
"""

BINARY_DIFF = """diff --git src://a/img.bin dst://a/img.bin
index 1111111..2222222 100644
Binary files src://a/img.bin and dst://a/img.bin differ
"""

MODIFY_DIFF = """diff --git src://a/b.c dst://a/b.c
index 1111111..2222222 100644
--- src://a/b.c
+++ dst://a/b.c
@@ -1,3 +1,3 @@
 int keep;
-int old;
+int added;
"""

ADD_DIFF = """diff --git src://a/new.c dst://a/new.c
new file mode 100644
--- /dev/null
+++ dst://a/new.c
@@ -0,0 +1,2 @@
+int x;
+int y;
"""


class ParseUnifiedDiffFidelityTest(unittest.TestCase):
    """解析器不能丢 section——丢了模型就完全不知道有过这个文件的变更。"""

    def test_deleted_file_is_kept(self):
        changes = parse_unified_diff(DELETE_DIFF)
        self.assertEqual(len(changes), 1)
        change = changes[0]
        self.assertEqual(change['type'], 'DELETE')
        self.assertEqual(change['new_path'], 'a/gone.c')
        self.assertEqual(change['deletions'], 3)
        self.assertEqual(change['additions'], 0)
        # 被删掉的内容必须保留，模型才知道删了什么
        self.assertIn('-int a;', change['diff'])

    def test_rename_is_kept(self):
        changes = parse_unified_diff(RENAME_DIFF)
        self.assertEqual(len(changes), 1)
        change = changes[0]
        self.assertEqual(change['type'], 'RENAME')
        self.assertEqual(change['old_path'], 'a/old.c')
        self.assertEqual(change['new_path'], 'a/new.c')
        self.assertIn('rename from a/old.c', change['diff'])
        self.assertIn('rename to a/new.c', change['diff'])

    def test_binary_is_kept(self):
        changes = parse_unified_diff(BINARY_DIFF)
        self.assertEqual(len(changes), 1)
        change = changes[0]
        self.assertEqual(change['type'], 'BINARY')
        self.assertEqual(change['new_path'], 'a/img.bin')
        self.assertEqual(change['additions'], 0)
        self.assertEqual(change['deletions'], 0)

    def test_modify_unchanged(self):
        changes = parse_unified_diff(MODIFY_DIFF)
        self.assertEqual(len(changes), 1)
        change = changes[0]
        self.assertEqual(change['type'], 'MODIFY')
        self.assertEqual(change['new_path'], 'a/b.c')
        self.assertEqual(change['additions'], 1)
        self.assertEqual(change['deletions'], 1)

    def test_add_unchanged(self):
        changes = parse_unified_diff(ADD_DIFF)
        self.assertEqual(len(changes), 1)
        change = changes[0]
        self.assertEqual(change['type'], 'ADD')
        self.assertEqual(change['new_path'], 'a/new.c')
        self.assertEqual(change['additions'], 2)

    def test_no_section_is_dropped(self):
        """混合 diff：五类 section 一个都不能少。"""
        raw = (DELETE_DIFF + RENAME_DIFF + BINARY_DIFF + MODIFY_DIFF + ADD_DIFF)
        changes = parse_unified_diff(raw)
        self.assertEqual(len(changes), 5)
        self.assertEqual(
            sorted(c['type'] for c in changes),
            sorted(['ADD', 'BINARY', 'DELETE', 'MODIFY', 'RENAME']),
        )

    def test_empty_diff(self):
        self.assertEqual(parse_unified_diff(None), [])
        self.assertEqual(parse_unified_diff(''), [])


class ReviewScopeTest(unittest.TestCase):
    def setUp(self):
        with patch("src.utils.code_reviewer.Factory"):
            self.reviewer = CodeReviewer()

    def _changes(self):
        return [
            {'new_path': 'a/one.c', 'old_path': 'a/one.c', 'type': 'MODIFY',
             'diff': '@@ -1 +1 @@\n+int a;', 'additions': 1, 'deletions': 0},
            {'new_path': 'a/two.c', 'old_path': 'a/two.c', 'type': 'DELETE',
             'diff': '@@ -1 +0 @@\n-int b;', 'additions': 0, 'deletions': 1},
            {'new_path': 'a/three.c', 'old_path': 'a/three.c', 'type': 'MODIFY',
             'diff': '@@ -1 +1 @@\n+int c;', 'additions': 1, 'deletions': 0},
        ]

    def test_scope_detects_dropped_files(self):
        """只送进去前两个文件时，第三个必须被算成未纳入。"""
        sent = ("diff --git a/a/one.c b/a/one.c\n--- a/a/one.c\n+++ b/a/one.c\n@@ -1 +1 @@\n+int a;\n"
                "diff --git a/a/two.c b/a/two.c\n--- a/a/two.c\n+++ /dev/null\n@@ -1 +0 @@\n-int b;\n")
        scope = self.reviewer.build_review_scope(self._changes(), sent)
        self.assertEqual(scope['total_files'], 3)
        self.assertEqual(len(scope['included_files']), 2)
        self.assertEqual(len(scope['excluded_files']), 1)
        self.assertEqual(scope['excluded_files'][0]['path'], 'a/three.c')

    def test_scope_counts_lines(self):
        sent = "diff --git a/a/one.c b/a/one.c\n@@ -1 +1 @@\n+int a;\n"
        scope = self.reviewer.build_review_scope(self._changes(), sent)
        self.assertEqual(scope['additions'], 2)
        self.assertEqual(scope['deletions'], 1)
        self.assertEqual(scope['sent_additions'], 1)
        self.assertEqual(scope['sent_deletions'], 0)

    def test_prompt_block_discloses_scope(self):
        sent = "diff --git a/a/one.c b/a/one.c\n@@ -1 +1 @@\n+int a;\n"
        scope = self.reviewer.build_review_scope(self._changes(), sent)
        block = self.reviewer._format_scope_prompt_block(scope)
        self.assertIn('变更文件总数：3', block)
        self.assertIn('实际送入审查：1 个文件', block)
        self.assertIn('未纳入本次审查的文件', block)
        self.assertIn('a/three.c', block)
        # 关键：必须明确禁止模型对没看到的文件下结论
        self.assertIn('不要推测未纳入文件的状况', block)

    def test_comment_header_has_metadata(self):
        sent = "diff --git a/a/one.c b/a/one.c\n@@ -1 +1 @@\n+int a;\n"
        scope = self.reviewer.build_review_scope(self._changes(), sent)
        header = self.reviewer._format_scope_comment_header(scope)
        self.assertIn('**Files Changed**: 3', header)
        self.assertIn('**Additions**: +2', header)
        self.assertIn('**Deletions**: -1', header)
        self.assertIn('**Reviewed**: 1 of 3 files (budget-limited)', header)
        self.assertIn('**Skipped**:', header)
        self.assertIn('a/three.c', header)

    def test_comment_header_without_exclusion(self):
        scope = self.reviewer.build_review_scope(
            self._changes(),
            "diff --git a/a/one.c b/a/one.c\n@@ -1 +1 @@\n+int a;\n"
            "diff --git a/a/two.c b/a/two.c\n@@ -1 +0 @@\n-int b;\n"
            "diff --git a/a/three.c b/a/three.c\n@@ -1 +1 @@\n+int c;\n")
        header = self.reviewer._format_scope_comment_header(scope)
        self.assertIn('**Reviewed**: 3 of 3 files', header)
        self.assertNotIn('budget-limited', header)

    def test_format_review_comment_combines(self):
        scope = self.reviewer.build_review_scope(
            self._changes(), "diff --git a/a/one.c b/a/one.c\n@@ -1 +1 @@\n+int a;\n")
        self.reviewer.last_review_scope = scope
        comment = self.reviewer.format_review_comment('模型报告正文')
        self.assertIn('## AI Code Review', comment)
        self.assertIn('模型报告正文', comment)
        self.assertIn('Auto Review Result', comment)


class ReviewInputCompositionTest(unittest.TestCase):
    """送进模型的输入里必须同时有覆盖度说明和仓库约定。"""

    def setUp(self):
        with patch("src.utils.code_reviewer.Factory"):
            self.reviewer = CodeReviewer()
        self.captured = {}

    def _fake_call_llm(self, messages):
        self.captured['messages'] = messages
        return '## 报告\n\n总分:88分'

    def test_prompt_contains_scope_and_repo_context(self):
        changes = [{'new_path': 'a/one.c', 'old_path': 'a/one.c', 'type': 'MODIFY',
                    'diff': '@@ -1 +1 @@\n+int a;', 'additions': 1, 'deletions': 0}]
        with patch.object(CodeReviewer, 'call_llm', side_effect=self._fake_call_llm):
            result = self.reviewer.review_and_strip_code(
                changes, 'fix something', changes,
                repo_context='### AGENTS.md\n端口命名必须用 P_/R_ 前缀')
        user_text = self.captured['messages'][1]['content']
        self.assertIn('本次审查范围', user_text)
        self.assertIn('仓库级约定', user_text)
        self.assertIn('P_/R_ 前缀', user_text)
        self.assertIn('总分:88分', result)
        self.assertTrue(self.reviewer.last_review_scope)
        self.assertEqual(self.reviewer.last_review_scope['total_files'], 1)

    def test_repo_context_absent_is_fine(self):
        changes = [{'new_path': 'a/one.c', 'old_path': 'a/one.c', 'type': 'MODIFY',
                    'diff': '@@ -1 +1 @@\n+int a;', 'additions': 1, 'deletions': 0}]
        with patch.object(CodeReviewer, 'call_llm', side_effect=self._fake_call_llm):
            self.reviewer.review_and_strip_code(changes, 'msg', changes)
        user_text = self.captured['messages'][1]['content']
        self.assertNotIn('仓库级约定', user_text)
        self.assertIn('本次审查范围', user_text)


class RepoContextReadTest(unittest.TestCase):
    """仓库级指令文件：有界读取，读不到不影响审查。"""

    def _handler(self):
        from src.bitbucket.webhook_handler import PullRequestHandler, BitbucketClient
        handler = PullRequestHandler.__new__(PullRequestHandler)
        handler.project_key = 'ICCC'
        handler.repository_slug = 'sw-1068'
        handler.target_branch = 'dev'
        handler.client = BitbucketClient('https://example.com', 'token')
        return handler

    def test_disabled_by_default(self):
        with patch.dict(os.environ, {'BITBUCKET_REPO_CONTEXT_FILES': ''}, clear=False):
            self.assertEqual(self._handler().get_repo_context(), '')

    def test_reads_and_bounds(self):
        env = {
            'BITBUCKET_REPO_CONTEXT_FILES': 'AGENTS.md',
            'BITBUCKET_REPO_CONTEXT_MAX_LINES': '3',
            'BITBUCKET_REPO_CONTEXT_MAX_BYTES': '10000',
        }
        with patch.dict(os.environ, env, clear=False), \
                patch('src.bitbucket.webhook_handler.BitbucketClient.get_file_content',
                      return_value='l1\nl2\nl3\nl4\nl5'):
            ctx = self._handler().get_repo_context()
        self.assertIn('### AGENTS.md', ctx)
        self.assertIn('l1', ctx)
        self.assertNotIn('l4', ctx)
        self.assertIn('已截断', ctx)

    def test_missing_file_returns_empty(self):
        env = {'BITBUCKET_REPO_CONTEXT_FILES': 'AGENTS.md'}
        with patch.dict(os.environ, env, clear=False), \
                patch('src.bitbucket.webhook_handler.BitbucketClient.get_file_content',
                      return_value=None):
            self.assertEqual(self._handler().get_repo_context(), '')

    def test_error_does_not_raise(self):
        env = {'BITBUCKET_REPO_CONTEXT_FILES': 'AGENTS.md'}
        with patch.dict(os.environ, env, clear=False), \
                patch('src.bitbucket.webhook_handler.BitbucketClient.get_file_content',
                      side_effect=RuntimeError('network down')):
            self.assertEqual(self._handler().get_repo_context(), '')

    def test_max_bytes_caps_output(self):
        env = {
            'BITBUCKET_REPO_CONTEXT_FILES': 'AGENTS.md',
            'BITBUCKET_REPO_CONTEXT_MAX_LINES': '1000',
            'BITBUCKET_REPO_CONTEXT_MAX_BYTES': '50',
        }
        with patch.dict(os.environ, env, clear=False), \
                patch('src.bitbucket.webhook_handler.BitbucketClient.get_file_content',
                      return_value='x' * 5000):
            ctx = self._handler().get_repo_context()
        body = ctx.split('\n', 1)[1]
        self.assertLessEqual(len(body.encode('utf-8')), 50)


if __name__ == "__main__":
    unittest.main()
