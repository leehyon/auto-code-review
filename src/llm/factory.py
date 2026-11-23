import os

from src.llm.client.base import BaseClient
from src.llm.client.deepseek import DeepSeekClient
from src.llm.client.github_copilot import GitHubCopilotClient
from src.llm.client.openai import OpenAIClient
from src.llm.client.qwen import QwenClient
from src.utils.log import logger


class Factory:
    @staticmethod
    def getClient(provider: str = None) -> BaseClient:
        provider = provider or os.getenv("LLM_PROVIDER", "openai")
        chat_model_providers = {
            'openai': lambda: OpenAIClient(),
            'deepseek': lambda: DeepSeekClient(),
            'qwen': lambda: QwenClient(),
            # 'github_copilot' 用的是完整实现（Responses API、token 交换、
            # endpoint 自动发现）；'copilot' 是 dev 分支上那个简化实现的别名，
            # 指向同一个类 —— 两个名字都留着，老配置不会因为合并而失效。
            'github_copilot': lambda: GitHubCopilotClient(),
            'copilot': lambda: GitHubCopilotClient(),
        }

        provider_func = chat_model_providers.get(provider)
        if provider_func:
            return provider_func()
        else:
            raise Exception(f'Unknown chat model provider: {provider}')
