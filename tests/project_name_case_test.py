"""项目名大小写统一。

各平台给的项目名大小写不一致（Bitbucket 的 display name 是大写、
slug 是小写），不统一的话同一个项目在统计和筛选里会裂成好几项。
"""
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class ProjectNameCaseTest(unittest.TestCase):

    def _mr(self, project_name):
        from src.entity.review_entity import MergeRequestReviewEntity
        return MergeRequestReviewEntity(
            project_name=project_name, author='a', source_branch='s', target_branch='t',
            updated_at=1, commits=[], score=1, url='u', review_result='r',
            url_slug='x', webhook_data={}, additions=0, deletions=0)

    def _push(self, project_name):
        from src.entity.review_entity import PushReviewEntity
        return PushReviewEntity(
            project_name=project_name, author='a', branch='b', updated_at=1,
            commits=[], score=1, review_result='r', url_slug='x',
            webhook_data={}, additions=0, deletions=0)

    def test_mr_entity_lowercases(self):
        self.assertEqual(self._mr('SW-1068-CMS-IDP-MCU').project_name,
                         'sw-1068-cms-idp-mcu')

    def test_push_entity_lowercases(self):
        self.assertEqual(self._push('SW-1068-CMS-IDP-MCU').project_name,
                         'sw-1068-cms-idp-mcu')

    def test_already_lowercase_unchanged(self):
        self.assertEqual(self._mr('sw-1068-cms-idp-mcu').project_name,
                         'sw-1068-cms-idp-mcu')

    def test_empty_is_safe(self):
        self.assertEqual(self._mr('').project_name, '')
        self.assertEqual(self._mr(None).project_name, '')


class ProjectFilterCaseTest(unittest.TestCase):
    """筛选按小写比较 —— 前端传大写也要能查到。"""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.db_file = os.path.join(self.tmpdir, 'test.db')
        self.original = os.environ.copy()
        os.environ.update({'DB_TYPE': 'sqlite', 'DB_FILE': self.db_file})
        from src.service.db_factory import DatabaseFactory
        DatabaseFactory.reload_config()
        from src.service.review_service import ReviewService
        ReviewService.init_db()
        self.service = ReviewService

        from src.entity.review_entity import MergeRequestReviewEntity
        entity = MergeRequestReviewEntity(
            project_name='SW-1068-CMS-IDP-MCU', author='a', source_branch='s',
            target_branch='t', updated_at=1, commits=[], score=90, url='u',
            review_result='## Summary\n- Review Score: 90\n', url_slug='x',
            webhook_data={}, additions=0, deletions=0)
        ReviewService.insert_mr_review_log(entity)

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self.original)
        from src.service.db_factory import DatabaseFactory
        DatabaseFactory.reload_config()
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_stored_lowercase(self):
        df = self.service.get_mr_review_logs()
        self.assertEqual(list(df['project_name']), ['sw-1068-cms-idp-mcu'])

    def test_filter_with_uppercase_finds_rows(self):
        df = self.service.get_mr_review_logs(project_names=['SW-1068-CMS-IDP-MCU'])
        self.assertEqual(len(df), 1)

    def test_filter_with_lowercase_finds_rows(self):
        df = self.service.get_mr_review_logs(project_names=['sw-1068-cms-idp-mcu'])
        self.assertEqual(len(df), 1)

    def test_only_one_project_group(self):
        """同一个项目不管大小写，统计里只应有一项。"""
        df = self.service.get_mr_review_logs()
        self.assertEqual(df['project_name'].nunique(), 1)


if __name__ == '__main__':
    unittest.main()
