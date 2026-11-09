"""token 计数与截断：编码器不可用时不能抛异常。

背景：tiktoken 首次 get_encoding 会从 openaipublic.blob.core.windows.net 下载 BPE 文件。
企业网络解析不了这个域名时，count_tokens 抛异常 → 整个审查中断 → PR 上没有评论。
"""
import os
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.utils import token_util


class TokenCountingTest(unittest.TestCase):

    def setUp(self):
        token_util._ENCODING_CACHE.clear()

    def tearDown(self):
        token_util._ENCODING_CACHE.clear()

    def test_count_tokens_normal(self):
        self.assertGreater(token_util.count_tokens("hello world"), 0)

    def test_count_tokens_survives_unavailable_encoding(self):
        """编码器拿不到时按字符估算，不能抛异常。"""
        with patch.object(token_util.tiktoken, 'get_encoding',
                          side_effect=OSError('network down')):
            n = token_util.count_tokens("int a = 1; void f(void) {}")
        self.assertGreater(n, 0)

    def test_estimate_tokens_scales_with_length(self):
        short = token_util.estimate_tokens("a" * 100)
        long = token_util.estimate_tokens("a" * 1000)
        self.assertGreater(long, short)

    def test_estimate_tokens_counts_cjk_as_denser(self):
        """中文比英文更占 token。"""
        en = token_util.estimate_tokens("a" * 100)
        zh = token_util.estimate_tokens("中" * 100)
        self.assertGreater(zh, en)

    def test_truncate_survives_unavailable_encoding(self):
        with patch.object(token_util.tiktoken, 'get_encoding',
                          side_effect=OSError('network down')):
            out = token_util.truncate_text_by_tokens("a" * 10000, 100)
        self.assertLess(len(out), 10000)
        self.assertGreater(len(out), 0)

    def test_truncate_keeps_short_text_untouched(self):
        with patch.object(token_util.tiktoken, 'get_encoding',
                          side_effect=OSError('network down')):
            text = "short text"
            self.assertEqual(token_util.truncate_text_by_tokens(text, 1000), text)

    def test_failed_encoding_is_cached_as_none(self):
        """失败的编码器要记住，不要每次调用都重试下载。"""
        with patch.object(token_util.tiktoken, 'get_encoding',
                          side_effect=OSError('network down')) as ge:
            token_util.get_encoding("cl100k_base")
            token_util.get_encoding("cl100k_base")
            self.assertEqual(ge.call_count, 1)

    def test_empty_text(self):
        self.assertEqual(token_util.count_tokens(""), 0)
        self.assertEqual(token_util.estimate_tokens(""), 0)


if __name__ == "__main__":
    unittest.main()
