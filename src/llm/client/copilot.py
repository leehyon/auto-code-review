"""GitHub Copilot 客户端的兼容入口。

这个模块最初是 Copilot 支持的第一个实现，功能比较简单（固定 endpoint、
只走 chat/completions、不做 token 交换）。现在完整实现在
``src/llm/client/github_copilot.py`` —— 它支持 Responses API、
GitHub token 换取短期 Copilot token、以及按订阅类型自动发现 endpoint。

保留这个文件是为了不破坏 ``from src.llm.client.copilot import CopilotClient``
这种引用（dev 分支上就是这个名字）。实现直接转过去，不维护第二份代码。
"""

from src.llm.client.github_copilot import GitHubCopilotClient


class CopilotClient(GitHubCopilotClient):
    """旧名别名，实际用的是 GitHubCopilotClient 的完整实现。"""
