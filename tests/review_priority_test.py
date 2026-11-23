"""文件审查优先级：源码在前，生成的模型/配置在后。

预算不够被顺序截断时，靠后的文件先被砍掉，源码尽量完整进入模型。
"""
import os
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.utils.code_reviewer import CodeReviewer


class ReviewPriorityTest(unittest.TestCase):

    def setUp(self):
        with patch("src.utils.code_reviewer.Factory"):
            self.reviewer = CodeReviewer()
        self._saved = os.environ.get('REVIEW_LOW_PRIORITY_EXTENSIONS')

    def tearDown(self):
        if self._saved is None:
            os.environ.pop('REVIEW_LOW_PRIORITY_EXTENSIONS', None)
        else:
            os.environ['REVIEW_LOW_PRIORITY_EXTENSIONS'] = self._saved

    def test_source_files_have_highest_priority(self):
        for ext in ('.c', '.h', '.cpp', '.hpp', '.py', '.java', '.go', '.ts', '.vue'):
            self.assertEqual(self.reviewer._file_review_priority(f'a/b{ext}'), 0, ext)

    def test_generated_files_have_lowest_priority(self):
        for ext in ('.arxml', '.xml'):
            self.assertEqual(self.reviewer._file_review_priority(f'a/b{ext}'), 2, ext)

    def test_other_files_are_middle(self):
        for ext in ('.md', '.yml', '.css', '.sql'):
            self.assertEqual(self.reviewer._file_review_priority(f'a/b{ext}'), 1, ext)

    def test_low_priority_list_is_configurable(self):
        """不同项目里「生成文件」的扩展名不一样，可以覆盖。"""
        os.environ['REVIEW_LOW_PRIORITY_EXTENSIONS'] = '.arxml,.xml,.generated'
        self.assertEqual(self.reviewer._file_review_priority('a/b.arxml'), 2)
        self.assertEqual(self.reviewer._file_review_priority('a/b.generated'), 2)
        self.assertEqual(self.reviewer._file_review_priority('a/b.c'), 0)

    def test_low_priority_list_can_be_emptied(self):
        os.environ['REVIEW_LOW_PRIORITY_EXTENSIONS'] = ''
        self.assertEqual(self.reviewer._file_review_priority('a/b.arxml'), 1)

    def test_sort_puts_source_first(self):
        changes = [
            {'new_path': 'a/Model.arxml'},
            {'new_path': 'a/main.c'},
            {'new_path': 'a/readme.md'},
            {'new_path': 'a/Model2.arxml'},
            {'new_path': 'a/util.cpp'},
            {'new_path': 'a/app.py'},
        ]
        ordered = [c['new_path'] for c in
                   self.reviewer._sort_changes_by_priority(changes)]
        self.assertEqual(ordered, [
            'a/main.c', 'a/util.cpp', 'a/app.py',     # 源码
            'a/readme.md',                              # 其它
            'a/Model.arxml', 'a/Model2.arxml',        # 生成的
        ])

    def test_sort_is_stable_within_same_priority(self):
        changes = [{'new_path': f'a/f{i}.c'} for i in range(5)]
        ordered = [c['new_path'] for c in
                   self.reviewer._sort_changes_by_priority(changes)]
        self.assertEqual(ordered, [c['new_path'] for c in changes])

    def test_sort_falls_back_to_old_path(self):
        changes = [{'old_path': 'a/deleted.c', 'new_path': ''},
                   {'new_path': 'a/Model.arxml'}]
        ordered = [c for c in self.reviewer._sort_changes_by_priority(changes)]
        self.assertEqual(ordered[0].get('old_path'), 'a/deleted.c')

    def test_diff_assembly_uses_priority_order(self):
        """组装进模型的 diff 必须是源码在前。"""
        captured = {}

        def fake_review_code(self, diffs_text, *args, **kwargs):
            captured['diff'] = diffs_text
            return '## Summary'

        changes = [
            {'new_path': 'a/Model.arxml', 'diff': '@@ -1 +1 @@\n+<X/>',
             'additions': 1, 'deletions': 0, 'type': 'MODIFY'},
            {'new_path': 'a/main.c', 'diff': '@@ -1 +1 @@\n+int a;',
             'additions': 1, 'deletions': 0, 'type': 'MODIFY'},
        ]
        with patch.object(CodeReviewer, 'review_code', fake_review_code):
            self.reviewer.review_and_strip_code(changes, 'msg', changes)
        diff = captured['diff']
        self.assertLess(diff.index('a/main.c'), diff.index('a/Model.arxml'))


if __name__ == "__main__":
    unittest.main()
