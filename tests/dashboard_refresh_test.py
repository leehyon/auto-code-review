"""仪表盘刷新路径的防回归测试。

背景（真实 bug）：
    停在「统计」tab 时点 Push / Merge Request，上面的统计卡变成了新类型的数据，
    下面的图表还留在旧类型的数据上 —— 同一个页面里两套数字对不上。
    根因是审查类型切换只调 loadData()，而 loadCharts() 只在 switchTab('charts') 里调。

这个测试用源码结构断言守住三条线：
    1. 审查类型切换走 refreshVisibleViews()，而不是只 loadData()
    2. refreshVisibleViews() 在统计 tab 可见时会一起刷新图表
    3. 记录表 / 统计卡 / 统计图用的是同一份已生效的筛选条件（appliedFilters）

和 i18n_test.py 一样，用正则/字符串解析 web/ 下的源码。
"""
import os
import re
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
APP_JS = os.path.join(ROOT, 'web', 'app.js')
INDEX_HTML = os.path.join(ROOT, 'web', 'index.html')


def _read(path):
    with open(path, encoding='utf-8') as f:
        return f.read()


def _function_body(js_text, name):
    """取 `function name(...) { ... }` 的函数体（按花括号配对）。"""
    m = re.search(r'function\s+' + re.escape(name) + r'\s*\(', js_text)
    if m is None:
        raise AssertionError(f'app.js 里找不到函数 {name}')
    start = js_text.index('{', m.end())
    depth = 0
    i = start
    while i < len(js_text):
        if js_text[i] == '{':
            depth += 1
        elif js_text[i] == '}':
            depth -= 1
            if depth == 0:
                return js_text[start + 1:i]
        i += 1
    raise AssertionError(f'函数 {name} 的花括号没配对')


class DashboardRefreshTest(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.js = _read(APP_JS)
        cls.html = _read(INDEX_HTML)

    def test_review_type_switch_refreshes_visible_views(self):
        """审查类型切换必须走 refreshVisibleViews，不能只刷记录表。"""
        anchor = 'input[name="reviewType"]'
        self.assertIn(anchor, self.js, '找不到审查类型 radio 的监听代码')
        block = self.js[self.js.index(anchor):self.js.index(anchor) + 800]
        self.assertIn('refreshVisibleViews', block,
                      '审查类型切换没有调用 refreshVisibleViews()')
        self.assertNotIn('=> loadData()', block,
                         '审查类型切换还在只调 loadData()，统计图不会跟着刷新')

    def test_refresh_visible_views_refreshes_charts_when_visible(self):
        """统计 tab 可见时，刷新要把图表一起刷。"""
        body = _function_body(self.js, 'refreshVisibleViews')
        self.assertIn('loadData(', body, 'refreshVisibleViews 没有刷新记录数据')
        self.assertIn('loadCharts(', body, 'refreshVisibleViews 没有刷新统计图')
        self.assertIn('isChartsVisible()', body,
                      'refreshVisibleViews 没有判断统计 tab 是否可见')

    def test_stats_and_records_share_applied_filters(self):
        """记录表 / 统计卡 / 统计图必须用同一份筛选条件。"""
        for name in ('loadData', 'loadCharts'):
            body = _function_body(self.js, name)
            self.assertIn('appliedFilters', body,
                          f'{name}() 没有沿用已生效的筛选条件，'
                          f'统计图和记录表可能显示不同批次的数据')

    def test_query_button_applies_filters(self):
        """查询按钮走 applyFilters()，输入框改了才生效。"""
        m = re.search(r'<button[^>]*onclick="(\w+)\(\)"[^>]*data-i18n="filter\.query"', self.html)
        if m is None:
            self.fail('找不到查询按钮')
            return
        self.assertEqual(m.group(1), 'applyFilters',
                         '查询按钮没有走 applyFilters()，统计图不会跟着刷新')

    def test_reset_and_language_switch_use_same_entry(self):
        """重置和语言切换也要经过同一条刷新路径。"""
        reset_body = _function_body(self.js, 'resetFilters')
        self.assertIn('applyFilters', reset_body, '重置筛选器没有走 applyFilters()')

        lang_body = _function_body(self.js, 'onLanguageChanged')
        self.assertIn('refreshVisibleViews', lang_body,
                      '语言切换没有走 refreshVisibleViews()，统计图文案不刷新')

    def test_is_charts_visible_matches_hidden_class(self):
        """isChartsVisible() 的判定要和 switchTab 的隐藏逻辑一致。"""
        body = _function_body(self.js, 'isChartsVisible')
        self.assertIn("tabContentCharts", body)
        self.assertIn("hidden", body)

        switch_body = _function_body(self.js, 'switchTab')
        self.assertIn("tabContentCharts", switch_body)
        self.assertIn("hidden", switch_body)


if __name__ == '__main__':
    unittest.main()
