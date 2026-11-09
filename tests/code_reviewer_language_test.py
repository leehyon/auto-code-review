"""审查提示词：逐文件语言判定、按文件类型分组检查点、输出契约。

重点回归：
- 混合语言的提交不能整份 diff 只用一套检查点
- 检查点必须按文件类型分组，每条只出现一次
- base 承载输出契约（分数、Risk Level、Questions 分节），profile 只放检查点
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

MIXED_DIFF = ARXML_DIFF + """diff --git a/Sources/MCAL/Can/Can.c b/Sources/MCAL/Can/Can.c
index 3333333..4444444 100644
--- a/Sources/MCAL/Can/Can.c
+++ b/Sources/MCAL/Can/Can.c
@@ -10,3 +10,3 @@
-void Can_Init(void) {}
+void Can_Init(void) { static int leak; }
diff --git a/Sources/BSW/Ecm/Ecm_Cfg.cpp b/Sources/BSW/Ecm/Ecm_Cfg.cpp
index 7777777..8888888 100644
--- a/Sources/BSW/Ecm/Ecm_Cfg.cpp
+++ b/Sources/BSW/Ecm/Ecm_Cfg.cpp
@@ -30,3 +30,3 @@
-void Ecm_Init() {}
+void Ecm_Init() { auto p = new int; }
diff --git a/Sources/BSW/Web/App.vue b/Sources/BSW/Web/App.vue
index 9999999..aaaaaaa 100644
--- a/Sources/BSW/Web/App.vue
+++ b/Sources/BSW/Web/App.vue
@@ -1,3 +1,3 @@
-<template><div/></template>
+<template><div @click="x++"/></template>
diff --git a/Sources/BSW/Web/api.ts b/Sources/BSW/Web/api.ts
index bbbbbbb..ccccccc 100644
--- a/Sources/BSW/Web/api.ts
+++ b/Sources/BSW/Web/api.ts
@@ -5,3 +5,3 @@
-export const load = () => 1;
+export const load = (): any => 1;
"""


class LanguageDetectionTest(unittest.TestCase):

    def setUp(self):
        with patch("src.utils.code_reviewer.Factory"):
            self.reviewer = CodeReviewer()

    def test_arxml_detected_as_autosar(self):
        self.assertEqual(self.reviewer._detect_language_from_diff(ARXML_DIFF), "autosar")

    def test_arxml_with_js_keywords_still_autosar(self):
        """核心回归：arxml 内容里的 import/const/function 不能让它变成 JavaScript。"""
        detected = self.reviewer._detect_language_from_diff(ARXML_WITH_JS_KEYWORDS)
        self.assertEqual(detected, "autosar")
        self.assertNotEqual(detected, "javascript")

    def test_per_file_detection(self):
        """逐文件判定，而不是整份 diff 投票。"""
        files = dict(self.reviewer._detect_file_languages(MIXED_DIFF))
        self.assertEqual(files['Configuration/SwcArxml/EMirror/Swc_Diagnostic.arxml'], 'autosar')
        self.assertEqual(files['Sources/MCAL/Can/Can.c'], 'c')
        self.assertEqual(files['Sources/BSW/Ecm/Ecm_Cfg.cpp'], 'cpp')
        self.assertEqual(files['Sources/BSW/Web/App.vue'], 'vue')
        self.assertEqual(files['Sources/BSW/Web/api.ts'], 'typescript')

    def test_per_file_detection_ignores_unknown_extension(self):
        diff = ("diff --git a/docs/readme.rst b/docs/readme.rst\n"
                "--- a/docs/readme.rst\n+++ b/docs/readme.rst\n@@ -1 +1 @@\n+x\n")
        self.assertEqual(self.reviewer._detect_file_languages(diff), [])


class CheckGroupingTest(unittest.TestCase):
    """混合提交按文件类型分组，不串味。"""

    def setUp(self):
        with patch("src.utils.code_reviewer.Factory"):
            self.reviewer = CodeReviewer()
        self.templates = self.reviewer._load_templates()

    def _groups(self, diff):
        checks = self.reviewer._build_profile_checks(
            self.reviewer._detect_file_languages(diff), self.templates)
        groups = {}
        current = None
        for line in checks.split('\n'):
            if line.startswith('### '):
                current = line[4:]
                groups[current] = []
            elif line.startswith('- ') and current:
                groups[current].append(line)
        return groups, checks

    def test_mixed_diff_gets_five_groups(self):
        groups, _ = self._groups(MIXED_DIFF)
        self.assertEqual(len(groups), 5)

    def test_each_file_type_gets_its_own_checks(self):
        groups, _ = self._groups(MIXED_DIFF)
        autosar = ' '.join(groups['AUTOSAR ARXML files (*.arxml)'])
        self.assertIn('Naming', autosar)
        self.assertIn('Port semantics', autosar)

        c_group = ' '.join(groups['C files (*.c)'])
        self.assertIn('MISRA C', c_group)
        self.assertIn('volatile', c_group)
        self.assertNotIn('MISRA C++', c_group)

        cpp_group = ' '.join(groups['C++ files (*.cpp, *.hpp, *.cc, *.cxx, *.h)'])
        self.assertIn('MISRA C++', cpp_group)
        self.assertIn('RAII', cpp_group)
        self.assertNotIn('VLAs', cpp_group)

    def test_vue_gets_both_frontend_groups(self):
        """.vue 同时适用通用前端和 Vue 专属两套检查点。"""
        groups, _ = self._groups(MIXED_DIFF)
        frontend = ' '.join(groups['Frontend files (*.js, *.ts, *.jsx, *.tsx, *.vue)'])
        vue = ' '.join(groups['Vue-specific files (*.vue)'])
        self.assertIn('Type safety', frontend)
        self.assertIn('Accessibility', frontend)
        self.assertIn('Reactivity', vue)
        self.assertIn('watchEffect', vue)

    def test_no_arxml_checks_leak_into_other_groups(self):
        groups, _ = self._groups(MIXED_DIFF)
        for title, lines in groups.items():
            if 'arxml' in title:
                continue
            body = ' '.join(lines)
            self.assertNotIn('AR-PACKAGE', body, f'{title} 混进了 ARXML 检查点')
            self.assertNotIn('Port semantics', body, f'{title} 混进了 ARXML 检查点')

    def test_each_check_appears_once(self):
        """同一条检查不能在两个组里重复出现。"""
        _, checks = self._groups(MIXED_DIFF)
        bullets = [ln.strip() for ln in checks.split('\n') if ln.strip().startswith('- ')]
        self.assertEqual(len(bullets), len(set(bullets)))

    def test_single_language_diff_gets_one_group(self):
        groups, _ = self._groups(ARXML_DIFF)
        self.assertEqual(list(groups.keys()), ['AUTOSAR ARXML files (*.arxml)'])

    def test_unmapped_files_fall_back_to_general(self):
        checks = self.reviewer._build_profile_checks([], self.templates)
        self.assertIn('Other files', checks)
        self.assertIn('Branch completeness', checks)


class BasePromptContractTest(unittest.TestCase):
    """base 承载输出契约，profile 只放检查点。"""

    def setUp(self):
        with patch("src.utils.code_reviewer.Factory"):
            self.reviewer = CodeReviewer()

    def test_base_carries_the_contract(self):
        system = self.reviewer._load_review_prompts()["system_message"]["content"]
        self.assertIn("Review Score", system)
        self.assertIn("Overall Risk Level", system)
        self.assertIn("Merge Recommendation", system)
        self.assertIn("Questions / Needs Confirmation", system)
        self.assertIn("Blocking Issues", system)
        self.assertIn("Do not invent file names", system)

    def test_base_has_no_language_checks(self):
        """检查点不该混进 base，否则每个语言都会带上别人的检查点。

        base 里出现 MISRA C++:2008 这类版本名是允许的——那是「不要混用规则版本」
        的清单，不是检查点条目。
        """
        system = self.reviewer._load_review_prompts()["system_message"]["content"]
        for check_only_in_a_profile in ('AR-PACKAGE', 'Reactivity', 'Type safety',
                                        'Branch completeness', 'VLAs',
                                        'standard compliance'):
            self.assertNotIn(check_only_in_a_profile, system)

    def test_user_prompt_has_all_slots(self):
        user = self.reviewer._load_review_prompts()["user_message"]["content"]
        for slot in ('{project_context}', '{review_scope}', '{profile_checks}',
                     '{diffs_text}', '{commits_text}'):
            self.assertIn(slot, user)

    def test_user_prompt_carries_no_style_or_format(self):
        user = self.reviewer._load_review_prompts()["user_message"]["content"]
        self.assertNotIn("{{ style }}", user)
        self.assertNotIn("Review Score", user)

    def test_style_is_applied(self):
        system = self.reviewer._load_review_prompts(style="concise")["system_message"]["content"]
        self.assertIn("concise", system)

    def test_unknown_style_falls_back(self):
        system = self.reviewer._load_review_prompts(style="nonsense")["system_message"]["content"]
        self.assertIn("professional, neutral tone", system)


class ReviewScoreParsingTest(unittest.TestCase):
    def test_review_score(self):
        self.assertEqual(CodeReviewer.parse_review_score("Review Score: 80"), 80)

    def test_review_score_with_out_of_hundred(self):
        self.assertEqual(CodeReviewer.parse_review_score("Review Score: 80/100"), 80)

    def test_bold_review_score(self):
        self.assertEqual(CodeReviewer.parse_review_score("**Review Score**: 80"), 80)

    def test_chinese_score_still_parses(self):
        """历史报告用的中文格式仍要能解析。"""
        self.assertEqual(CodeReviewer.parse_review_score("总分:80分"), 80)

    def test_total_issues_is_not_mistaken_for_score(self):
        self.assertEqual(
            CodeReviewer.parse_review_score("- Total Issues: 3\n- Blocking Issues: 1"), 0)

    def test_no_score(self):
        self.assertEqual(CodeReviewer.parse_review_score("no issues found"), 0)


if __name__ == "__main__":
    unittest.main()
