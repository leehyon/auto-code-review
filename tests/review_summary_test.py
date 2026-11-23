"""审查报告只存 Summary 段 —— 测试 + 体积对比。"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

REPORT = """## Summary
- Overall Risk Level: HIGH
- Total Issues: 6
- Blocking Issues: 3
- Review Score: 34
- Merge Recommendation: REQUEST_CHANGES

The changes add Dynamic Zoom and PLS mode. The principal risk is a fixed test speed.

## Issues

### [HIGH] Dynamic zoom uses a fixed test speed
- Severity: HIGH
- Category: Bug
- Location: Sources/ASW/Functions/Display.c:120

#### 🐛 Problem
It hardcodes the speed.

#### 🔍 Evidence
```c
setSpeed(30);
```

#### ⚡ Impact
Zoom runs too fast.

#### 💡 Recommendation
Read the speed from the signal.

## Questions / Needs Confirmation

### Definition of the new RTE shared variable
- Location: `Rte_Display.h`
"""


class ExtractSummaryTest(unittest.TestCase):

    def setUp(self):
        from src.utils.review_summary import extract_summary
        self.extract = extract_summary

    def test_keeps_summary_section(self):
        out = self.extract(REPORT)
        self.assertTrue(out.startswith('## Summary'))
        self.assertIn('Review Score: 34', out)
        self.assertIn('Merge Recommendation: REQUEST_CHANGES', out)
        self.assertIn('The changes add Dynamic Zoom', out)

    def test_drops_issues_section(self):
        out = self.extract(REPORT)
        self.assertNotIn('## Issues', out)
        self.assertNotIn('Dynamic zoom uses a fixed test speed', out)

    def test_drops_questions_section(self):
        out = self.extract(REPORT)
        self.assertNotIn('Questions / Needs Confirmation', out)

    def test_empty_input(self):
        self.assertEqual(self.extract(''), '')
        self.assertEqual(self.extract(None), '')

    def test_report_without_summary_is_truncated(self):
        """异常输出没有 Summary 段时，退回截断而不是整篇入库。"""
        out = self.extract('x' * 5000)
        self.assertEqual(len(out), 1000)

    def test_summary_at_end_of_report(self):
        out = self.extract('## Summary\n- Review Score: 90\n')
        self.assertIn('Review Score: 90', out)

    def test_summary_is_much_smaller(self):
        """Summary 段应该只占全文的一小部分。"""
        out = self.extract(REPORT)
        self.assertLess(len(out), len(REPORT) * 0.5)

    def test_summary_ratio_on_realistic_report(self):
        """真实报告的 Issues 段很长，Summary 只占零头。"""
        big_issues = '\n'.join(
            f'### [HIGH] Issue number {i}\n' + ('detail line\n' * 20)
            for i in range(20))
        report = '## Summary\n- Review Score: 40\n\n概述。\n\n## Issues\n' + big_issues
        out = self.extract(report)
        self.assertLess(len(out), len(report) * 0.1)


class ServiceStoresSummaryTest(unittest.TestCase):
    """入库时只写 Summary 段。"""

    def setUp(self):
        import tempfile
        self.tmpdir = tempfile.mkdtemp()
        self.db_file = os.path.join(self.tmpdir, 'test.db')
        self.original = os.environ.copy()
        os.environ.update({'DB_TYPE': 'sqlite', 'DB_FILE': self.db_file})
        from src.service.db_factory import DatabaseFactory
        DatabaseFactory.reload_config()
        from src.service.review_service import ReviewService
        ReviewService.init_db()
        self.service = ReviewService

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self.original)
        from src.service.db_factory import DatabaseFactory
        DatabaseFactory.reload_config()
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_insert_stores_only_summary(self):
        from src.entity.review_entity import MergeRequestReviewEntity
        entity = MergeRequestReviewEntity(
            project_name='P', author='a', source_branch='s', target_branch='t',
            updated_at=1, commits=[], score=34, url='u', review_result=REPORT,
            url_slug='x', webhook_data={}, additions=0, deletions=0)
        self.service.insert_mr_review_log(entity)

        from src.service.db_factory import get_db_connection
        with get_db_connection() as conn:
            row = conn.fetchone('SELECT review_result FROM mr_review_log')
        stored = row[0] if row else ''
        self.assertIn('Review Score: 34', stored)
        self.assertNotIn('## Issues', stored)
        self.assertLess(len(stored), len(REPORT) * 0.5)

    def test_entity_keeps_full_result_in_memory(self):
        """实体本身保留完整报告 —— 通知和评论要用全文。"""
        from src.entity.review_entity import MergeRequestReviewEntity
        entity = MergeRequestReviewEntity(
            project_name='P', author='a', source_branch='s', target_branch='t',
            updated_at=1, commits=[], score=34, url='u', review_result=REPORT,
            url_slug='x', webhook_data={}, additions=0, deletions=0)
        self.assertEqual(entity.review_result, REPORT)
        self.assertIn('## Issues', entity.review_result)


if __name__ == '__main__':
    unittest.main()
