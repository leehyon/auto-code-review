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
    """按领域给一段方向提示，不逐条罗列检查点。"""

    def setUp(self):
        with patch("src.utils.code_reviewer.Factory"):
            self.reviewer = CodeReviewer()
        self.templates = self.reviewer._load_templates()

    def _checks(self, diff):
        return self.reviewer._build_profile_checks(
            self.reviewer._detect_file_languages(diff), self.templates)

    def test_mixed_diff_gets_two_domain_blocks(self):
        """C/C++/ARXML 归一段 automotive，前端归一段 frontend。"""
        checks = self._checks(MIXED_DIFF)
        self.assertIn('This review may contain C, C++ and AUTOSAR ARXML files', checks)
        self.assertIn('This review may contain JavaScript, TypeScript and Vue files', checks)
        self.assertEqual(checks.count('This review may contain'), 2)

    def test_automotive_block_covers_c_cpp_and_arxml_together(self):
        """C、C++、ARXML 同属 automotive，不拆成三段。"""
        checks = self._checks(MIXED_DIFF)
        block = checks.split('This review may contain C, C++ and AUTOSAR ARXML files')[1]
        block = block.split('This review may contain')[0]
        for topic in ('undefined behavior', 'lifetime', 'concurrency', 'real-time',
                      'port direction', 'ComSpec', 'Runnable mappings',
                      'Cross-file consistency'):
            self.assertIn(topic, block)

    def test_automotive_block_defers_naming_to_project_context(self):
        checks = self._checks(MIXED_DIFF)
        flat = ' '.join(checks.split())
        self.assertIn('Project-specific naming rules apply only when explicitly '
                      'provided in Project Context', flat)
        self.assertIn('recommend changing its source model or generation '
                      'configuration', flat)

    def test_frontend_block_is_one_paragraph_for_js_ts_vue(self):
        checks = self._checks(MIXED_DIFF)
        self.assertIn('Type safety and unjustified `any`', checks)
        self.assertIn('Accessibility of the affected UI', checks)

    def test_no_exhaustive_checklist_in_profile(self):
        """profile 是方向段，不是检查项清单——条目化会诱导模型凑问题。"""
        for name, profile in self.templates['review_profiles'].items():
            bullets = [ln for ln in profile['checks'].split('\n') if ln.strip().startswith('- ')]
            self.assertLessEqual(len(bullets), 6,
                                 f'{name} 列了 {len(bullets)} 条，超出方向段的定位')

    def test_single_language_diff_gets_one_block(self):
        checks = self._checks(ARXML_DIFF)
        self.assertEqual(checks.count('This review may contain'), 1)
        self.assertIn('AUTOSAR ARXML files', checks)

    def test_unmapped_files_fall_back_to_general(self):
        checks = self.reviewer._build_profile_checks([], self.templates)
        self.assertIn('files in several languages', checks)
        self.assertIn('Correctness and undefined behavior', checks)

    def test_xml_files_are_ignored_for_domain_selection(self):
        """.xml 不参与领域判定：它可能是 pom.xml 之类，套 automotive 方向是噪音。"""
        diff = ("diff --git a/build/pom.xml b/build/pom.xml\n"
                "--- a/build/pom.xml\n+++ b/build/pom.xml\n@@ -1 +1 @@\n+<x/>\n"
                "diff --git a/Sources/a.c b/Sources/a.c\n"
                "--- a/Sources/a.c\n+++ b/Sources/a.c\n@@ -1 +1 @@\n+int a;\n")
        files = dict(self.reviewer._detect_file_languages(diff))
        self.assertNotIn('build/pom.xml', files)
        self.assertEqual(files['Sources/a.c'], 'c')

        checks = self._checks(diff)
        self.assertIn('This review may contain C, C++ and AUTOSAR ARXML files', checks)

    def test_xml_only_diff_falls_back_to_general(self):
        """只有 .xml 时不该拉进 automotive 方向。"""
        diff = ("diff --git a/build/pom.xml b/build/pom.xml\n"
                "--- a/build/pom.xml\n+++ b/build/pom.xml\n@@ -1 +1 @@\n+<x/>\n")
        checks = self._checks(diff)
        self.assertNotIn('AUTOSAR', checks)
        self.assertIn('files in several languages', checks)


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

    def test_base_has_no_domain_specific_guidance(self):
        """base 必须与语言和领域无关，任何领域的具体内容都不该出现在里面。"""
        system = self.reviewer._load_review_prompts()["system_message"]["content"]
        for domain_specific in ('AR-PACKAGE', 'ComSpec', 'Runnable', 'MISRA',
                                'AUTOSAR', 'Type safety', 'goroutine', 'Vue',
                                'React', 'unhandled rejections'):
            self.assertNotIn(domain_specific, system)

    def test_base_is_short(self):
        """base 只留审查方法和输出协议，不能越写越长。"""
        system = self.reviewer._load_review_prompts()["system_message"]["content"]
        self.assertLess(len(system), 6000)
        self.assertLess(len(system.split('\n')), 170)

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
        self.assertIn("standard engineering terminology", system)


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


class ReportSectionEmojiTest(unittest.TestCase):
    """issue 内部的小节用 emoji 做视觉锚点，顶层标题不加。"""

    def setUp(self):
        import yaml
        with open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                               'config', 'prompt_templates.yml'), encoding='utf-8') as f:
            self.templates = yaml.safe_load(f)
        self.system_prompt = self.templates['base_review_prompt']['system_prompt']

    def test_issue_subsections_have_emoji(self):
        for header in ['#### 🐛 Problem', '#### 🚧 Standard Violation',
                       '#### 🔍 Evidence', '#### ⚡ Impact', '#### 💡 Recommendation']:
            self.assertIn(header, self.system_prompt,
                          f'base prompt 缺少带 emoji 的小节标题: {header}')

    def test_top_level_sections_have_no_emoji(self):
        """顶层标题已经是 H2，再加 emoji 是噪音。"""
        for header in ['## Summary', '## Issues']:
            self.assertIn(header, self.system_prompt)
        self.assertNotIn('## Summary 🐛', self.system_prompt)
        self.assertNotIn('## Issues 🐛', self.system_prompt)

    def test_issue_title_has_no_emoji(self):
        """严重度方括号本身就是锚点，标题行不再叠 emoji。"""
        self.assertIn('### [<Severity>] <Short Title>', self.system_prompt)


if __name__ == "__main__":
    unittest.main()
