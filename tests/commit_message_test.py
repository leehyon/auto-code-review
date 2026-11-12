"""提交信息（commit_messages）的取值规则。

入库的提交信息 = 过滤合并提交 -> 只取 subject -> 只留前 2 个 + 其余计数。
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class CommitSubjectTest(unittest.TestCase):

    def _entity(self, commits):
        from src.entity.review_entity import MergeRequestReviewEntity
        return MergeRequestReviewEntity(
            project_name='P', author='a', source_branch='s', target_branch='t',
            updated_at=1, commits=commits, score=100, url='u', review_result='r',
            url_slug='x', webhook_data={}, additions=0, deletions=0)

    def test_prefers_title(self):
        e = self._entity([{'title': 'feat: add thing', 'message': 'feat: add thing\n\nbody here'}])
        self.assertEqual(e.commit_messages, 'feat: add thing')

    def test_falls_back_to_first_line_of_message(self):
        e = self._entity([{'message': 'fix: broken thing\nlong body\nmore'}])
        self.assertEqual(e.commit_messages, 'fix: broken thing')

    def test_multiline_body_is_trimmed(self):
        """正文里的换行不能漏进提交信息 —— 那会让表格单元格里出现多行。"""
        e = self._entity([{'message': 'subject line\n\nbody\nmore body'}])
        self.assertNotIn('\n', e.commit_messages)

    def test_empty_commits(self):
        self.assertEqual(self._entity([]).commit_messages, '')

    def test_skips_commits_without_any_message(self):
        e = self._entity([{'title': 'ok'}, {'title': ''}, {'message': ''}])
        self.assertEqual(e.commit_messages, 'ok')

    def test_push_entity_uses_same_rule(self):
        from src.entity.review_entity import PushReviewEntity
        e = PushReviewEntity(
            project_name='P', author='a', branch='b', updated_at=1,
            commits=[{'message': 'push subject\nbody'}], score=100,
            review_result='r', url_slug='x', webhook_data={}, additions=0, deletions=0)
        self.assertEqual(e.commit_messages, 'push subject')


class MergeCommitFilterTest(unittest.TestCase):
    """合并提交要过滤掉 —— 它们只是同步主干，不是真正的改动。"""

    def _entity(self, commits):
        from src.entity.review_entity import MergeRequestReviewEntity
        return MergeRequestReviewEntity(
            project_name='P', author='a', source_branch='s', target_branch='t',
            updated_at=1, commits=commits, score=100, url='u', review_result='r',
            url_slug='x', webhook_data={}, additions=0, deletions=0)

    def test_filters_merge_branch(self):
        e = self._entity([
            {'title': 'real change'},
            {'title': "Merge branch 'dev' into feat/ICCC-17-ispmgr-development"},
        ])
        self.assertEqual(e.commit_messages, 'real change')

    def test_filters_merge_pull_request(self):
        e = self._entity([
            {'title': 'real change'},
            {'title': 'Merge pull request #123 from user/branch'},
        ])
        self.assertEqual(e.commit_messages, 'real change')

    def test_filters_by_multiple_parents(self):
        """最准的判断：合并提交有多个父节点。"""
        e = self._entity([
            {'title': 'real change'},
            {'title': 'some merge', 'parents': [{'id': 'a'}, {'id': 'b'}]},
        ])
        self.assertEqual(e.commit_messages, 'real change')

    def test_single_parent_is_not_merge(self):
        e = self._entity([
            {'title': 'real change', 'parents': [{'id': 'a'}]},
        ])
        self.assertEqual(e.commit_messages, 'real change')

    def test_commit_mentioning_merge_is_kept(self):
        """只是标题里带 merge 字样的普通提交不能被误杀。"""
        e = self._entity([{'title': 'fix merge conflict in display module'}])
        self.assertEqual(e.commit_messages, 'fix merge conflict in display module')

    def test_only_merge_commits_gives_empty(self):
        e = self._entity([{'title': "Merge branch 'dev' into feat/x"}])
        self.assertEqual(e.commit_messages, '')


class StoredCommitLimitTest(unittest.TestCase):
    """只存前 2 个 subject，其余用计数表示。"""

    def _entity(self, commits):
        from src.entity.review_entity import MergeRequestReviewEntity
        return MergeRequestReviewEntity(
            project_name='P', author='a', source_branch='s', target_branch='t',
            updated_at=1, commits=commits, score=100, url='u', review_result='r',
            url_slug='x', webhook_data={}, additions=0, deletions=0)

    def test_one_commit(self):
        self.assertEqual(self._entity([{'title': 'a'}]).commit_messages, 'a')

    def test_two_commits(self):
        self.assertEqual(self._entity([{'title': 'a'}, {'title': 'b'}]).commit_messages, 'a; b')

    def test_three_commits_gets_count(self):
        e = self._entity([{'title': 'a'}, {'title': 'b'}, {'title': 'c'}])
        self.assertEqual(e.commit_messages, 'a; b +1')

    def test_count_excludes_merge_commits(self):
        """计数是过滤后的有效提交数，不是原始数量。"""
        e = self._entity([
            {'title': 'a'},
            {'title': 'b'},
            {'title': 'c'},
            {'title': "Merge branch 'dev' into feat/x"},
            {'title': "Merge branch 'dev' into feat/x"},
        ])
        self.assertEqual(e.commit_messages, 'a; b +1')

    def test_stored_text_is_short(self):
        """存进库的是压缩后的短文本，不是全量。"""
        e = self._entity([{'title': f'commit number {i}'} for i in range(50)])
        self.assertEqual(e.commit_messages, 'commit number 0; commit number 1 +48')
        self.assertLess(len(e.commit_messages), 60)


if __name__ == '__main__':
    unittest.main()
