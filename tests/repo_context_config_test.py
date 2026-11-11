"""仓库约定文件的开关与按项目指定规则地址。

三个环境变量：
- BITBUCKET_REPO_CONTEXT_ENABLED       0 = 完全关闭
- BITBUCKET_REPO_CONTEXT_FILES          全局清单（留空 = 自动发现）
- BITBUCKET_REPO_CONTEXT_PROJECT_FILES  按项目指定，格式 PROJECT_KEY:path1,path2
"""
import os
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class RepoContextConfigTest(unittest.TestCase):

    def _handler(self, project_key='ICCC', repo='sw-repo'):
        from src.bitbucket.webhook_handler import PullRequestHandler
        repository = {'slug': repo, 'name': repo, 'project': {'key': project_key}}
        payload = {
            'eventKey': 'pr:opened',
            'pullRequest': {
                'id': 1,
                'title': 't',
                'description': '',
                'fromRef': {'id': 'refs/heads/feat', 'displayId': 'feat',
                            'repository': repository},
                'toRef': {'id': 'refs/heads/dev', 'displayId': 'dev',
                          'repository': repository},
                'author': {'name': 'alice'},
                'state': 'OPEN',
                'closed': False,
                'mergeable': True,
            },
            'repository': repository,
        }
        return PullRequestHandler(payload, 'token', 'https://bb.example.com')

    def _patch_client(self, existing):
        """existing: {文件名: 内容}，不在其中的文件返回 None。"""
        requested = []

        def side_effect(path, ref=None):
            requested.append(path)
            for name, content in existing.items():
                if path.endswith('/raw/' + name):
                    return content
            return None

        return requested, side_effect

    def _run(self, env, existing):
        requested, side_effect = self._patch_client(existing)
        full_env = {
            'BITBUCKET_REPO_CONTEXT_ENABLED': '1',
            'BITBUCKET_REPO_CONTEXT_FILES': '',
            'BITBUCKET_REPO_CONTEXT_PROJECT_FILES': '',
        }
        full_env.update(env)
        with patch.dict(os.environ, full_env, clear=False), \
                patch('src.bitbucket.webhook_handler.BitbucketClient.get_file_content',
                      side_effect=side_effect):
            ctx = self._handler().get_repo_context()
        return ctx, requested

    # ---------- 开关 ----------

    def test_disabled_makes_no_requests(self):
        """开关关掉后不应该发任何请求，也不该占上下文。"""
        ctx, requested = self._run(
            {'BITBUCKET_REPO_CONTEXT_ENABLED': '0'},
            {'AGENTS.md': '规则内容'})
        self.assertEqual(ctx, '')
        self.assertEqual(requested, [], '关闭后仍发了请求')

    def test_disabled_accepts_various_false_values(self):
        for value in ('0', 'false', 'False', 'no', 'NO'):
            ctx, requested = self._run(
                {'BITBUCKET_REPO_CONTEXT_ENABLED': value},
                {'AGENTS.md': '规则内容'})
            self.assertEqual(ctx, '', f'ENABLED={value} 时应该关闭')
            self.assertEqual(requested, [])

    def test_enabled_by_default(self):
        """没配开关时默认是开的。"""
        ctx, requested = self._run(
            {'BITBUCKET_REPO_CONTEXT_ENABLED': '1'},
            {'AGENTS.md': '规则内容'})
        self.assertIn('规则内容', ctx)
        self.assertTrue(requested)

    # ---------- 按项目指定 ----------

    def test_project_override_takes_precedence(self):
        """项目条目命中时用项目指定的文件，而不是全局清单。"""
        ctx, requested = self._run(
            {
                'BITBUCKET_REPO_CONTEXT_FILES': 'global.md',
                'BITBUCKET_REPO_CONTEXT_PROJECT_FILES': 'ICCC:docs/rules.md',
            },
            {'docs/rules.md': '项目专属规则', 'global.md': '全局规则'})
        self.assertIn('项目专属规则', ctx)
        self.assertNotIn('全局规则', ctx)
        self.assertEqual(len(requested), 1)
        self.assertTrue(requested[0].endswith('/raw/docs/rules.md'))

    def test_project_override_is_case_insensitive(self):
        ctx, requested = self._run(
            {'BITBUCKET_REPO_CONTEXT_PROJECT_FILES': 'iccc:docs/rules.md'},
            {'docs/rules.md': '项目专属规则'})
        self.assertIn('项目专属规则', ctx)

    def test_other_project_falls_back_to_global(self):
        """项目条目不匹配当前项目时，回落到全局清单。"""
        ctx, requested = self._run(
            {
                'BITBUCKET_REPO_CONTEXT_FILES': 'global.md',
                'BITBUCKET_REPO_CONTEXT_PROJECT_FILES': 'OTHER:other.md',
            },
            {'global.md': '全局规则', 'other.md': '别的项目规则'})
        self.assertIn('全局规则', ctx)
        self.assertNotIn('别的项目规则', ctx)

    def test_multiple_projects_in_one_value(self):
        """多个项目用分号分隔。"""
        ctx, requested = self._run(
            {'BITBUCKET_REPO_CONTEXT_PROJECT_FILES': 'OTHER:other.md;ICCC:mine.md'},
            {'mine.md': '我的规则', 'other.md': '别人的规则'})
        self.assertIn('我的规则', ctx)
        self.assertNotIn('别人的规则', ctx)

    def test_project_override_supports_multiple_files(self):
        """项目条目里可以用逗号列多个文件，且全部读取。"""
        ctx, requested = self._run(
            {'BITBUCKET_REPO_CONTEXT_PROJECT_FILES': 'ICCC:a.md,b.md'},
            {'a.md': '规则A', 'b.md': '规则B'})
        self.assertIn('规则A', ctx)
        self.assertIn('规则B', ctx)
        self.assertEqual(len(requested), 2)

    def test_project_override_reads_from_target_branch(self):
        """约定文件必须从目标分支读，不能从源分支读。"""
        refs = []

        def side_effect(path, ref=None):
            refs.append(ref)
            return '规则内容'

        env = {
            'BITBUCKET_REPO_CONTEXT_ENABLED': '1',
            'BITBUCKET_REPO_CONTEXT_FILES': 'rules.md',
            'BITBUCKET_REPO_CONTEXT_PROJECT_FILES': '',
        }
        with patch.dict(os.environ, env, clear=False), \
                patch('src.bitbucket.webhook_handler.BitbucketClient.get_file_content',
                      side_effect=side_effect):
            self._handler().get_repo_context()
        self.assertTrue(refs)
        self.assertTrue(all(r == 'dev' for r in refs),
                        f'应该用目标分支 dev，实际 {refs}')

    # ---------- 自动发现 ----------

    def test_auto_discovery_reads_only_one_file(self):
        """自动发现时只读第一个命中的，README 不再每次都进上下文。"""
        ctx, requested = self._run(
            {},
            {'AGENTS.md': '规则A', 'CLAUDE.md': '规则B', 'README.md': '项目简介'})
        self.assertIn('规则A', ctx)
        self.assertNotIn('规则B', ctx)
        self.assertNotIn('项目简介', ctx)
        self.assertEqual(len(requested), 1)

    def test_auto_discovery_falls_back_to_readme(self):
        """没有强约定文件时才读 README。

        请求次数不能减少——要确认 Tier 1 全部落空才能回落到 Tier 2。
        但关键是只有 README 的内容进了上下文。
        """
        ctx, requested = self._run(
            {},
            {'README.md': '项目简介'})
        self.assertIn('项目简介', ctx)
        self.assertNotIn('### CONTRIBUTING.md', ctx)
        # Tier 1 全部探测完，再探 Tier 2
        self.assertEqual(len(requested), 9)

    def test_explicit_global_list_reads_all(self):
        """显式配置的清单全部读取——那是配置者的明确意图。"""
        ctx, requested = self._run(
            {'BITBUCKET_REPO_CONTEXT_FILES': 'a.md,b.md'},
            {'a.md': '规则A', 'b.md': '规则B'})
        self.assertIn('规则A', ctx)
        self.assertIn('规则B', ctx)
        self.assertEqual(len(requested), 2)

    def test_missing_file_is_skipped(self):
        """读不到就跳过，不能让审查失败。"""
        ctx, requested = self._run(
            {'BITBUCKET_REPO_CONTEXT_FILES': 'missing.md'},
            {})
        self.assertEqual(ctx, '')


if __name__ == "__main__":
    unittest.main()
