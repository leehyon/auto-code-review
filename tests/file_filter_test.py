"""SUPPORTED_EXTENSIONS 解析：空项不能把过滤搞失效。"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.utils.file_filter import supported_extensions, is_supported


class SupportedExtensionsTest(unittest.TestCase):

    def setUp(self):
        self._saved = os.environ.get('SUPPORTED_EXTENSIONS')

    def tearDown(self):
        if self._saved is None:
            os.environ.pop('SUPPORTED_EXTENSIONS', None)
        else:
            os.environ['SUPPORTED_EXTENSIONS'] = self._saved

    def test_parses_and_strips(self):
        os.environ['SUPPORTED_EXTENSIONS'] = '.java, .arxml ,.c'
        self.assertEqual(supported_extensions(), ['.java', '.arxml', '.c'])

    def test_trailing_comma_does_not_disable_filtering(self):
        """空元素会让 endswith('') 恒真，所有文件都会被审查。"""
        os.environ['SUPPORTED_EXTENSIONS'] = '.java,'
        self.assertEqual(supported_extensions(), ['.java'])
        self.assertFalse(is_supported('a/Dcm_V.xml'))
        self.assertTrue(is_supported('a/Demo.java'))

    def test_double_comma_does_not_disable_filtering(self):
        os.environ['SUPPORTED_EXTENSIONS'] = '.java,,.c'
        self.assertEqual(supported_extensions(), ['.java', '.c'])
        self.assertFalse(is_supported('a/anything.txt'))

    def test_xml_not_supported_by_default_config(self):
        """默认配置里没有 .xml，普通 XML 文件不该被审查。"""
        os.environ['SUPPORTED_EXTENSIONS'] = (
            '.java,.py,.php,.yml,.vue,.go,.c,.cpp,.h,.js,.css,.md,.sql,'
            '.ts,.jsx,.tsx,.arxml')
        self.assertFalse(is_supported('a/Dcm_V.xml'))
        self.assertFalse(is_supported('build/pom.xml'))
        self.assertTrue(is_supported('a/Model.arxml'))
        self.assertTrue(is_supported('a/x.c'))

    def test_empty_list_matches_nothing(self):
        self.assertFalse(is_supported('a/x.java', extensions=[]))


if __name__ == "__main__":
    unittest.main()
