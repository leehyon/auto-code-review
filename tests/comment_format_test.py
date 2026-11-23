"""审查评论的首行：@ 提醒用户。"""
import os
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.utils.comment_format import (
    build_review_comment,
    mention_users,
    review_comment_prefix,
)


class MentionUsersTest(unittest.TestCase):

    def setUp(self):
        self._saved = os.environ.get('REVIEW_MENTION_USERS')

    def tearDown(self):
        if self._saved is None:
            os.environ.pop('REVIEW_MENTION_USERS', None)
        else:
            os.environ['REVIEW_MENTION_USERS'] = self._saved

    def test_parses_comma_separated(self):
        os.environ['REVIEW_MENTION_USERS'] = 'junchu1,alice'
        self.assertEqual(mention_users(), ['junchu1', 'alice'])

    def test_strips_whitespace_and_at_sign(self):
        os.environ['REVIEW_MENTION_USERS'] = ' junchu1 , @alice '
        self.assertEqual(mention_users(), ['junchu1', 'alice'])

    def test_empty_means_no_mentions(self):
        os.environ['REVIEW_MENTION_USERS'] = ''
        self.assertEqual(mention_users(), [])

    def test_unset_means_no_mentions(self):
        os.environ.pop('REVIEW_MENTION_USERS', None)
        self.assertEqual(mention_users(), [])

    def test_only_separators_means_no_mentions(self):
        os.environ['REVIEW_MENTION_USERS'] = ' , , '
        self.assertEqual(mention_users(), [])


class ReviewCommentPrefixTest(unittest.TestCase):

    def setUp(self):
        self._saved = os.environ.get('REVIEW_MENTION_USERS')

    def tearDown(self):
        if self._saved is None:
            os.environ.pop('REVIEW_MENTION_USERS', None)
        else:
            os.environ['REVIEW_MENTION_USERS'] = self._saved

    def test_prefix_with_mentions(self):
        os.environ['REVIEW_MENTION_USERS'] = 'junchu1,alice'
        self.assertEqual(
            review_comment_prefix(),
            'Auto Review Result, @junchu1 @alice FYI:')

    def test_prefix_without_mentions(self):
        os.environ['REVIEW_MENTION_USERS'] = ''
        self.assertEqual(review_comment_prefix(), 'Auto Review Result:')

    def test_prefix_has_no_at_when_empty(self):
        os.environ['REVIEW_MENTION_USERS'] = ''
        self.assertNotIn('@', review_comment_prefix())


class BuildReviewCommentTest(unittest.TestCase):

    def setUp(self):
        self._saved = os.environ.get('REVIEW_MENTION_USERS')

    def tearDown(self):
        if self._saved is None:
            os.environ.pop('REVIEW_MENTION_USERS', None)
        else:
            os.environ['REVIEW_MENTION_USERS'] = self._saved

    def test_comment_shape(self):
        os.environ['REVIEW_MENTION_USERS'] = 'junchu1,alice'
        comment = build_review_comment('## Summary\nOverall Risk Level: HIGH')
        self.assertTrue(comment.startswith('Auto Review Result, @junchu1 @alice FYI:\n'))
        self.assertIn('## Summary', comment)

    def test_comment_without_mentions(self):
        os.environ['REVIEW_MENTION_USERS'] = ''
        comment = build_review_comment('## Summary')
        self.assertTrue(comment.startswith('Auto Review Result:\n'))
        self.assertNotIn('@', comment.split('\n')[0])

    def test_no_ai_code_review_title(self):
        """开头不能有 `## AI Code Review` 标题。"""
        os.environ['REVIEW_MENTION_USERS'] = 'junchu1'
        comment = build_review_comment('## Summary')
        self.assertNotIn('## AI Code Review', comment)

    def test_reviewer_uses_shared_formatter(self):
        """CodeReviewer.format_review_comment 必须走同一套首行逻辑。"""
        from src.utils.code_reviewer import CodeReviewer
        os.environ['REVIEW_MENTION_USERS'] = 'junchu1,alice'
        with patch('src.utils.code_reviewer.Factory'):
            reviewer = CodeReviewer()
        comment = reviewer.format_review_comment('## Summary')
        self.assertTrue(comment.startswith('Auto Review Result, @junchu1 @alice FYI:'))


if __name__ == "__main__":
    unittest.main()
