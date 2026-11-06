"""CodeReviewer 语言识别与提示词选择测试。

重点回归：AUTOSAR arxml / XML 文件不能因为内容里出现 import、const、function
这类关键字而被误判成 JavaScript。
"""
import os
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.utils.code_reviewer import CodeReviewer


ARXML_DIFF = """diff --git a/Configuration/SwcArxml/EMirror/Swc_Diagnostic.arxml b/Configuration/SwcArxml/EMirror/Swc_Diagnostic.arxml
index 0000000..0000000 100644
--- a/Configuration/SwcArxml/EMirror/Swc_Diagnostic.arxml
+++ b/Configuration/SwcArxml/EMirror/Swc_Diagnostic.arxml
@@ -5,7 +5,7 @@
-                  <SHORT-NAME>Diagnostics</SHORT-NAME>
+                  <SHORT-NAME>Diagnostic</SHORT-NAME>
                   <ELEMENTS>
                     <APPLICATION-SW-COMPONENT-TYPE UUID="b8bcc0f2-8598-46ec-9721-7041a25691be">
                       <SHORT-NAME>Diagnostic</SHORT-NAME>
"""

# arxml 里常见的、会被内容启发式误判成 JS/Python 的关键字
ARXML_WITH_JS_KEYWORDS = """diff --git a/Configuration/SwcArxml/EMirror/EMirror.arxml b/Configuration/SwcArxml/EMirror/EMirror.arxml
index 0000000..0000000 100644
--- a/Configuration/SwcArxml/EMirror/EMirror.arxml
+++ b/Configuration/SwcArxml/EMirror/EMirror.arxml
@@ -1,4 +1,4 @@
+            <IMPORT>NONE</IMPORT>
+            <CONSTANT-SW-CONTINUATION>const value</CONSTANT-SW-CONTINUATION>
+            <SHORT-NAME>function_placeholder</SHORT-NAME>
+            <DESC>let it be</DESC>
"""

XML_DIFF = """diff --git a/Sources/BSW/NeuSar/Persistence/Module/src_Dcm/Dcm_V.xml b/Sources/BSW/NeuSar/Persistence/Module/src_Dcm/Dcm_V.xml
index 0000000..0000000 100644
--- a/Sources/BSW/NeuSar/Persistence/Module/src_Dcm/Dcm_V.xml
+++ b/Sources/BSW/NeuSar/Persistence/Module/src_Dcm/Dcm_V.xml
@@ -1,3 +1,3 @@
+      <VALUE>0x01</VALUE>
"""

JS_DIFF = """diff --git a/src/app.ts b/src/app.ts
index 0000000..0000000 100644
--- a/src/app.ts
+++ b/src/app.ts
@@ -1,3 +1,4 @@
+const value = 1;
+export function handler() { return value; }
"""

# 没有可解析文件路径的裸代码内容
RAW_JS_SNIPPET = "const x = 1;\nfunction foo() { return x; }\n"


class LanguageDetectionTest(unittest.TestCase):
    def setUp(self):
        # 语言识别与提示词选择都不依赖 LLM，把 client 构造打掉让测试与环境配置无关
        with patch("src.utils.code_reviewer.Factory"):
            self.reviewer = CodeReviewer()

    def test_arxml_detected_as_autosar(self):
        self.assertEqual(self.reviewer._detect_language_from_diff(ARXML_DIFF), "autosar")

    def test_arxml_with_js_keywords_still_autosar(self):
        """核心回归：arxml 内容里的 import/const/function 不能让它变成 JavaScript。"""
        detected = self.reviewer._detect_language_from_diff(ARXML_WITH_JS_KEYWORDS)
        self.assertEqual(detected, "autosar")
        self.assertNotEqual(detected, "javascript")

    def test_xml_detected_as_xml(self):
        self.assertEqual(self.reviewer._detect_language_from_diff(XML_DIFF), "xml")

    def test_typescript_still_detected(self):
        self.assertEqual(self.reviewer._detect_language_from_diff(JS_DIFF), "typescript")

    def test_content_heuristic_still_used_when_no_file_path(self):
        """没有文件路径时才允许内容启发式，原有行为不能被破坏。"""
        self.assertEqual(self.reviewer._detect_language_from_diff(RAW_JS_SNIPPET), "javascript")

    def test_unknown_extension_falls_back_to_default(self):
        """扩展名不认识时回到 default，而不是被内容关键字带偏。"""
        diff = """diff --git a/docs/readme.rst b/docs/readme.rst
index 0000000..0000000 100644
--- a/docs/readme.rst
+++ b/docs/readme.rst
@@ -1,2 +1,2 @@
+const x = 1;
"""
        self.assertEqual(self.reviewer._detect_language_from_diff(diff), "default")

    def test_detect_from_changes_arxml(self):
        changes = [{"new_path": "Configuration/SwcArxml/EMirror/EMirror.arxml"}]
        self.assertEqual(self.reviewer._detect_language_from_changes(changes), "autosar")

    def test_detect_from_changes_xml(self):
        changes = [{"new_path": "Sources/BSW/NeuSar/Persistence/Module/src_Dcm/Dcm_V.xml"}]
        self.assertEqual(self.reviewer._detect_language_from_changes(changes), "xml")

    def test_detect_from_changes_unknown_extension(self):
        changes = [{"new_path": "docs/readme.rst"}]
        self.assertEqual(self.reviewer._detect_language_from_changes(changes), "default")


class PromptSelectionTest(unittest.TestCase):
    def setUp(self):
        with patch("src.utils.code_reviewer.Factory"):
            self.reviewer = CodeReviewer()

    def test_autosar_maps_to_autosar_prompt(self):
        self.assertEqual(
            self.reviewer.language_prompts["autosar"], "autosar_review_prompt"
        )

    def test_xml_and_default_map_to_generic_prompt(self):
        self.assertEqual(self.reviewer.language_prompts["xml"], "code_review_prompt")
        self.assertEqual(self.reviewer.language_prompts["default"], "code_review_prompt")

    def test_get_appropriate_prompt_for_arxml(self):
        self.assertEqual(
            self.reviewer._get_appropriate_prompt(ARXML_WITH_JS_KEYWORDS),
            "autosar_review_prompt",
        )

    def test_autosar_prompt_template_loads(self):
        """新增的 autosar_review_prompt 必须能被加载，且带 {diffs_text}/{commits_text}。"""
        prompts = self.reviewer._load_language_specific_prompts("autosar_review_prompt")
        self.assertIn("AUTOSAR", prompts["system_message"]["content"])
        self.assertIn("总分", prompts["system_message"]["content"])
        self.assertIn("{diffs_text}", prompts["user_message"]["content"])
        self.assertIn("{commits_text}", prompts["user_message"]["content"])


if __name__ == "__main__":
    unittest.main()
