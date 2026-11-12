"""前端多语言翻译表的完整性检查。

翻译表在 web/i18n.js 里（JS 对象），这里用正则解析出来核对：
- 中英文的 key 必须一一对应，不能有一边漏翻
- HTML 里 data-i18n 引用的 key 必须存在
- app.js 里 t('key') 引用的 key 必须存在

漏翻译时前端会退回中文（不至于白屏），但英文界面就会混着中文，
这个测试用来提前发现。
"""
import os
import re
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
I18N_JS = os.path.join(ROOT, 'web', 'i18n.js')
INDEX_HTML = os.path.join(ROOT, 'web', 'index.html')
APP_JS = os.path.join(ROOT, 'web', 'app.js')


def _read(path):
    with open(path, encoding='utf-8') as f:
        return f.read()


def _parse_table(js_text, lang):
    """从 I18N 对象里抽出某个语言的 key -> value。"""
    # 定位 `zh: {` / `en: {` 的花括号块
    marker = f'\n  {lang}: {{'
    start = js_text.index(marker) + len(marker)
    depth = 1
    i = start
    while depth > 0:
        if js_text[i] == '{':
            depth += 1
        elif js_text[i] == '}':
            depth -= 1
        i += 1
    block = js_text[start:i - 1]
    pairs = re.findall(r"'([^']+)'\s*:\s*'((?:[^'\\]|\\.)*)'", block)
    return {k: v for k, v in pairs}


class I18nTableTest(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.js = _read(I18N_JS)
        cls.zh = _parse_table(cls.js, 'zh')
        cls.en = _parse_table(cls.js, 'en')

    def test_both_tables_are_parsed(self):
        self.assertGreater(len(self.zh), 30, '中文表解析出来的条目太少')
        self.assertGreater(len(self.en), 30, '英文表解析出来的条目太少')

    def test_same_keys_in_both_languages(self):
        only_zh = set(self.zh) - set(self.en)
        only_en = set(self.en) - set(self.zh)
        self.assertEqual(only_zh, set(), f'这些 key 只有中文没有英文: {sorted(only_zh)}')
        self.assertEqual(only_en, set(), f'这些 key 只有英文没有中文: {sorted(only_en)}')

    def test_no_empty_translations(self):
        for lang, table in (('zh', self.zh), ('en', self.en)):
            empty = [k for k, v in table.items() if not v.strip()]
            self.assertEqual(empty, [], f'{lang} 有空译文: {empty}')

    def test_english_has_no_chinese_left(self):
        """英文表里不该残留中文（品牌名、专有名词除外）。"""
        allowed = ('Merge Request', 'Push')
        leftovers = []
        for k, v in self.en.items():
            if any('\u4e00' <= c <= '\u9fff' for c in v):
                if not any(a in v for a in allowed):
                    leftovers.append((k, v))
        self.assertEqual(leftovers, [], f'英文译文里还有中文: {leftovers}')

    def test_html_i18n_keys_exist(self):
        html = _read(INDEX_HTML)
        keys = set(re.findall(r'data-i18n(?:-title|-aria)?="([^"]+)"', html))
        self.assertGreater(len(keys), 20, 'HTML 里的 data-i18n 标注太少')
        missing = {k for k in keys if k not in self.zh}
        self.assertEqual(missing, set(), f'HTML 引用了不存在的 key: {sorted(missing)}')

    def test_script_t_keys_exist(self):
        js = _read(APP_JS)
        keys = set(re.findall(r"\bt\('([^']+)'", js))
        self.assertGreater(len(keys), 10, 'app.js 里的 t() 调用太少')
        missing = {k for k in keys if k not in self.zh}
        self.assertEqual(missing, set(), f'app.js 引用了不存在的 key: {sorted(missing)}')

    def test_title_unchanged(self):
        """主标题「Code Review」不参与翻译。"""
        html = _read(INDEX_HTML)
        self.assertIn('<h1>Code Review</h1>', html)
        self.assertNotIn('data-i18n="meta.title"', html)

    def test_language_switch_present(self):
        html = _read(INDEX_HTML)
        self.assertIn('data-lang-btn="zh"', html)
        self.assertIn('data-lang-btn="en"', html)
        self.assertIn('i18n.js', html)


if __name__ == '__main__':
    unittest.main()
