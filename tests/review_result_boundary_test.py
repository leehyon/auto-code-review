"""review_result 的边界：数据库保留 Summary，不下发给前端。"""
import json
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

REPORT = """## Summary
- Overall Risk Level: HIGH
- Total Issues: 6
- Blocking Issues: 3
- Review Score: 34
- Merge Recommendation: REQUEST_CHANGES

概述段落。

## Issues

### [HIGH] 某个问题
- Severity: HIGH
"""


class ReviewResultBoundaryTest(unittest.TestCase):

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
            project_name='P', author='a', source_branch='s', target_branch='t',
            updated_at=1, commits=[{'title': 'c1'}], score=34, url='u',
            review_result=REPORT, url_slug='x', webhook_data={},
            additions=10, deletions=2)
        ReviewService.insert_mr_review_log(entity)

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self.original)
        from src.service.db_factory import DatabaseFactory
        DatabaseFactory.reload_config()
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_database_keeps_summary(self):
        """数据库里要留着 Summary 段 —— 评论被删了还能查。"""
        from src.service.db_factory import get_db_connection
        with get_db_connection() as conn:
            row = conn.fetchone('SELECT review_result FROM mr_review_log')
        stored = row[0] if row else ''
        self.assertIn('## Summary', stored)
        self.assertIn('Review Score: 34', stored)
        self.assertNotIn('## Issues', stored)

    def test_list_query_does_not_select_review_result(self):
        """列表查询根本不取这一列 —— 少从数据库搬一次。"""
        df = self.service.get_mr_review_logs()
        self.assertNotIn('review_result', df.columns)
        self.assertIn('score', df.columns)

    def test_api_response_excludes_review_result(self):
        """接口响应里不能有 review_result。"""
        import ui_server
        client = ui_server.ui_app.test_client()
        resp = client.get('/api/review/logs?type=mr')
        self.assertEqual(resp.status_code, 200)
        payload = json.loads(resp.data.decode())
        self.assertEqual(len(payload['data']), 1)
        self.assertNotIn('review_result', payload['data'][0])

    def test_api_response_still_has_statistics(self):
        """剔掉报告不影响统计字段。"""
        import ui_server
        client = ui_server.ui_app.test_client()
        payload = json.loads(client.get('/api/review/logs?type=mr').data.decode())
        row = payload['data'][0]
        self.assertEqual(row['score'], 34)
        self.assertEqual(row['project_name'], 'p')
        self.assertEqual(payload['total'], 1)
        self.assertEqual(payload['average_score'], 34)

    def test_single_record_still_has_review_result(self):
        """按 id 取单条记录（重试路径）仍然要拿得到 —— 那里需要原始上下文。"""
        record = self.service.get_review_log('mr', 1)
        self.assertIsNotNone(record)
        self.assertIn('review_result', record or {})


if __name__ == '__main__':
    unittest.main()
