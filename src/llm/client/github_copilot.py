"""
GitHub Copilot LLM 客户端。

Copilot 的接口是 OpenAI 兼容的，但和普通 OpenAI 有几处不同（都是实测出来的）：

1. **API 形态**
   - Responses API：``POST {base}/responses``，请求头要带 ``OpenAI-Intent: responses``，
     body 用 ``input`` 而不是 ``messages``。
   - Chat Completions：``POST {base}/chat/completions``，body 用 ``messages``。

   **部分模型（如 gpt-6-luna）只支持 Responses API**，走 chat/completions 会返回
   ``unsupported_api_for_model``。所以默认走 Responses API，
   可用 ``GITHUB_COPILOT_API_MODE=chat_completions`` 切换。

   Responses 这一路是**直接用 requests 发**的，没有走 openai SDK：SDK 的 ``client.responses``
   要 openai>=1.66 才有，而本项目 requirements.txt 钉的是 openai==1.59.3，
   直接用 SDK 会在生产环境抛 AttributeError。chat_completions 那一路仍然走 SDK。

2. **token**
   - GitHub token（``ghp_`` / ``gho_`` / ``ghu_`` / ``ghs_`` / ``ghr_`` / ``github_pat_`` 开头）：
     理论上可以用 ``https://api.github.com/copilot_internal/v2/token`` 换一张短期
     （约 30 分钟）的 Copilot token。
     **但实测部分订阅类型（business）换 token 会直接 403**，这时候**原始 token 直接当
     Bearer 用是可行的**。所以这里的策略是：换 token 失败就回退到原始 token，
     并且记住这次失败，后面不再重复请求。
   - 已经换好的 Copilot token（不以上述前缀开头）：直接用。

3. **endpoint**
   不同订阅类型指向不同的域名。可以用
   ``GET https://api.github.com/copilot_internal/user`` 查出来（``endpoints.api``），
   例如 business 账号是 ``https://api.business.githubcopilot.com``。
   没显式配置 ``GITHUB_COPILOT_API_BASE_URL`` 时会自动去查，查不到就用默认地址。

参考：
- https://github.com/dengyiling/copilot-api
- https://docs.github.com/en/copilot/how-tos/use-copilot-agents
"""

import os
import time
from typing import Dict, List, Optional, Union

import requests
from openai import OpenAI

from src.llm.client.base import BaseClient
from src.llm.types import NotGiven, NOT_GIVEN
from src.utils.log import logger

# GitHub token 的前缀（PAT / OAuth / GitHub App），这类 token 可能会被换成短期 Copilot token
GITHUB_TOKEN_PREFIXES = ('ghp_', 'gho_', 'ghu_', 'ghs_', 'ghr_', 'github_pat_')
# 用 GitHub token 换 Copilot token 的接口
DEFAULT_COPILOT_TOKEN_URL = 'https://api.github.com/copilot_internal/v2/token'
# 查询账号信息和真实 endpoint 的接口
DEFAULT_COPILOT_USER_URL = 'https://api.github.com/copilot_internal/user'
# Copilot token 过期前多少秒就提前刷新
TOKEN_REFRESH_MARGIN = 60
# 直接给出的 Copilot token 假定还有多久有效期（仅用于控制缓存时长）
ASSUMED_TOKEN_LIFETIME = 25 * 60

# 支持的 API 形态
API_MODE_RESPONSES = 'responses'
API_MODE_CHAT_COMPLETIONS = 'chat_completions'
DEFAULT_API_MODE = API_MODE_RESPONSES


class GitHubCopilotClient(BaseClient):
    """GitHub Copilot 客户端。"""

    def __init__(self, api_key: str = None):
        self.api_key = api_key or os.getenv("GITHUB_COPILOT_TOKEN")
        self.base_url = os.getenv("GITHUB_COPILOT_API_BASE_URL",
                                  "https://api.githubcopilot.com").rstrip("/")
        self.default_model = os.getenv("GITHUB_COPILOT_MODEL", "gpt-4o")
        self.api_mode = os.getenv("GITHUB_COPILOT_API_MODE", DEFAULT_API_MODE).strip().lower()
        if self.api_mode not in (API_MODE_RESPONSES, API_MODE_CHAT_COMPLETIONS):
            logger.warn(f'Unknown GITHUB_COPILOT_API_MODE "{self.api_mode}", '
                        f'falling back to {DEFAULT_API_MODE}.')
            self.api_mode = DEFAULT_API_MODE
        # Copilot 的边缘节点会校验这些编辑器标识，缺失会返回 403
        self.editor_version = os.getenv("GITHUB_COPILOT_EDITOR_VERSION", "vscode/1.95.0")
        self.integration_id = os.getenv("GITHUB_COPILOT_INTEGRATION_ID", "vscode-chat")
        self.user_agent = os.getenv("GITHUB_COPILOT_USER_AGENT", "GitHubCopilotChat/0.26.7")
        self.token_url = os.getenv("GITHUB_COPILOT_TOKEN_URL", DEFAULT_COPILOT_TOKEN_URL)
        self.user_url = os.getenv("GITHUB_COPILOT_USER_URL", DEFAULT_COPILOT_USER_URL)
        self.timeout = float(os.getenv("GITHUB_COPILOT_TIMEOUT", 180))

        self._copilot_token: Optional[str] = None
        self._copilot_token_expires_at: float = 0.0
        # 换 token 接口是否可用。实测部分订阅类型（business）会返回 403，
        # 这时原始 GitHub token 直接当 Bearer 用反而是可行的，所以要记住失败只试一次。
        self._exchange_unavailable = False
        self.client: Optional[OpenAI] = None
        self._client_token: Optional[str] = None

    # ------------------------------------------------------------------
    # token 处理
    # ------------------------------------------------------------------

    @staticmethod
    def _is_github_token(token: Optional[str]) -> bool:
        """判断是不是 GitHub token（PAT / OAuth / GitHub App）。"""
        return bool(token) and token.startswith(GITHUB_TOKEN_PREFIXES)

    def _discover_endpoint(self, github_token: str) -> Optional[str]:
        """
        查询账号信息，拿到该订阅真正对应的 Copilot endpoint。

        不同订阅类型指向不同的域名，例如 business 账号是
        ``https://api.business.githubcopilot.com``，individual 是
        ``https://api.individual.githubcopilot.com``。

        失败就返回 None，调用方回退到默认地址。
        """
        try:
            response = requests.get(self.user_url, headers={
                "Authorization": f"token {github_token}",
                "Accept": "application/json",
                "User-Agent": self.user_agent,
            }, timeout=30)
        except Exception as e:
            logger.warn(f"Failed to query the GitHub Copilot account info: {e}")
            return None

        if response.status_code != 200:
            logger.warn(f"Failed to query the GitHub Copilot account info: "
                        f"{response.status_code}")
            return None

        try:
            data = response.json()
        except Exception as e:
            logger.warn(f"Failed to parse the GitHub Copilot account info: {e}")
            return None

        endpoint = (data.get("endpoints") or {}).get("api")
        if endpoint:
            logger.info(f"GitHub Copilot endpoint: {endpoint}")
        return endpoint

    def _exchange_token(self, github_token: str) -> Optional[str]:
        """用 GitHub token 换取短期 Copilot token。"""
        try:
            response = requests.get(self.token_url, headers={
                "Authorization": f"Bearer {github_token}",
                "Accept": "application/vnd.github+json",
                "User-Agent": self.user_agent,
            }, timeout=30)
        except Exception as e:
            logger.error(f"Failed to request a Copilot token: {e}")
            return None

        if response.status_code != 200:
            logger.error(f"Failed to exchange the GitHub token for a Copilot token: "
                         f"{response.status_code} {response.text[:200]}")
            return None

        try:
            data = response.json()
        except Exception as e:
            logger.error(f"Failed to parse the Copilot token response: {e}")
            return None

        token = data.get("token")
        if not token:
            logger.error("The Copilot token response does not contain a token.")
            return None

        expires_at = data.get("expires_at")
        try:
            self._copilot_token_expires_at = float(expires_at) if expires_at else 0.0
        except (TypeError, ValueError):
            self._copilot_token_expires_at = 0.0
        if not self._copilot_token_expires_at:
            self._copilot_token_expires_at = time.time() + ASSUMED_TOKEN_LIFETIME

        logger.info(f"Obtained a Copilot token, valid until {self._copilot_token_expires_at}.")
        return token

    def _resolve_token(self, force_refresh: bool = False) -> Optional[str]:
        """
        拿到可以调用 Copilot API 的 token。

        优先使用 GITHUB_COPILOT_TOKEN；没有配置时回退到 GITHUB_TOKEN。
        """
        if not force_refresh and self._copilot_token and \
                time.time() < self._copilot_token_expires_at - TOKEN_REFRESH_MARGIN:
            return self._copilot_token

        source = self.api_key or os.getenv("GITHUB_TOKEN", "")
        if not source:
            logger.error("Neither GITHUB_COPILOT_TOKEN nor GITHUB_TOKEN is configured.")
            return None

        if self._is_github_token(source):
            # 显式配置了地址就不再去发现，用用户指定的
            if not os.getenv("GITHUB_COPILOT_API_BASE_URL"):
                endpoint = self._discover_endpoint(source)
                if endpoint:
                    self.base_url = endpoint.rstrip("/")

            if not self._exchange_unavailable:
                token = self._exchange_token(source)
                if token:
                    self._copilot_token = token
                    return token
                # 换 token 不可用（实测部分订阅会返回 403），记住这次失败，
                # 后面直接用原始 token 当 Bearer，不再重复请求
                self._exchange_unavailable = True
                logger.warn("Falling back to using the GitHub token directly as the bearer token.")

            token = source
            self._copilot_token_expires_at = time.time() + ASSUMED_TOKEN_LIFETIME
        else:
            # 已经是 Copilot token，直接用
            token = source
            self._copilot_token_expires_at = time.time() + ASSUMED_TOKEN_LIFETIME

        self._copilot_token = token
        return token

    def _get_client(self, token: str) -> OpenAI:
        """按当前 token 创建/复用 OpenAI 兼容客户端（chat/completions 那一路用）。"""
        if self.client is None or self._client_token != token:
            self.client = OpenAI(
                api_key=token,
                base_url=self.base_url,
                default_headers=self._headers("conversation-panel"),
                timeout=self.timeout,
            )
            self._client_token = token
        return self.client

    def _headers(self, intent: str) -> Dict[str, str]:
        """构造 Copilot 需要的请求头。"""
        return {
            "Authorization": f"Bearer {self._copilot_token}",
            "Content-Type": "application/json",
            "Editor-Version": self.editor_version,
            "Copilot-Integration-Id": self.integration_id,
            "User-Agent": self.user_agent,
            "OpenAI-Intent": intent,
        }

    # ------------------------------------------------------------------
    # 调用
    # ------------------------------------------------------------------

    def completions(self,
                    messages: List[Dict[str, str]],
                    model: Union[Optional[str], NotGiven] = NOT_GIVEN,
                    ) -> str:
        model = model or self.default_model
        token = self._resolve_token()
        if not token:
            raise ValueError("No usable GitHub Copilot token, check GITHUB_COPILOT_TOKEN.")
        self._copilot_token = token

        if self.api_mode == API_MODE_CHAT_COMPLETIONS:
            return self._completions_chat(messages, model)
        return self._completions_responses(messages, model)

    def _completions_responses(self, messages: List[Dict[str, str]], model: str) -> str:
        """走 Responses API：POST {base}/responses，body 用 input。"""
        url = f"{self.base_url}/responses"
        payload = {"model": model, "input": messages}
        headers = self._headers("responses")

        try:
            response = requests.post(url, headers=headers, json=payload, timeout=self.timeout)
        except Exception as e:
            logger.error(f"GitHub Copilot Responses API error: {e}")
            raise

        if response.status_code != 200:
            logger.error(f"GitHub Copilot Responses API error: "
                         f"{response.status_code} {response.text[:300]}")
            raise ValueError(f"GitHub Copilot Responses API returned {response.status_code}: "
                             f"{response.text[:300]}")

        try:
            data = response.json()
        except Exception as e:
            logger.error(f"Failed to parse the GitHub Copilot response: {e}")
            raise

        text = self._extract_output_text(data)
        if not text:
            logger.error("GitHub Copilot returned an empty response.")
            raise ValueError("GitHub Copilot returned an empty response.")
        return text

    @staticmethod
    def _extract_output_text(data: dict) -> str:
        """
        从 Responses API 的返回里取文本。

        ``output_text`` 是 SDK 自己拼出来的便捷字段，原始 JSON 里不一定有，
        所以还要从 ``output[].content[].text`` 里兜底取。
        """
        text = data.get("output_text")
        if text:
            return text

        parts = []
        for item in data.get("output") or []:
            if not isinstance(item, dict):
                continue
            for content in item.get("content") or []:
                if not isinstance(content, dict):
                    continue
                if content.get("type") not in (None, "output_text", "text"):
                    continue
                value = content.get("text")
                if value:
                    parts.append(value)
        return "\n".join(parts)

    def _completions_chat(self, messages: List[Dict[str, str]], model: str) -> str:
        """走 Chat Completions API：POST {base}/chat/completions，body 用 messages。"""
        client = self._get_client(self._copilot_token)
        try:
            completion = client.chat.completions.create(
                model=model,
                messages=messages,
            )
        except Exception as e:
            status_code = getattr(getattr(e, "response", None), "status_code", None)
            if status_code == 401:
                # token 过期或配错了，换一个 token 再试一次
                logger.warn("GitHub Copilot returned 401, refreshing the token and retrying.")
                # 允许再试一次换 token（上次可能是暂时性的失败）
                self._exchange_unavailable = False
                token = self._resolve_token(force_refresh=True)
                if not token:
                    raise
                self._copilot_token = token
                client = self._get_client(token)
                completion = client.chat.completions.create(
                    model=model,
                    messages=messages,
                )
            elif status_code == 404:
                raise ValueError(
                    f'GitHub Copilot does not know the model "{model}". '
                    f"Check GITHUB_COPILOT_MODEL, or list the available models with "
                    f"GET {self.base_url}/models.") from e
            else:
                raise

        if not completion.choices:
            raise ValueError("GitHub Copilot returned an empty response.")
        return completion.choices[0].message.content or ""

    def list_models(self) -> List[str]:
        """列出 Copilot 当前可用的模型，用于排查模型名配错的问题。"""
        token = self._resolve_token()
        if not token:
            return []
        try:
            response = requests.get(f"{self.base_url}/models", headers={
                "Authorization": f"Bearer {token}",
                "Editor-Version": self.editor_version,
                "Copilot-Integration-Id": self.integration_id,
                "User-Agent": self.user_agent,
                "Accept": "application/json",
            }, timeout=30)
        except Exception as e:
            logger.error(f"Failed to list the GitHub Copilot models: {e}")
            return []

        if response.status_code != 200:
            logger.error(f"Failed to list the GitHub Copilot models: "
                         f"{response.status_code} {response.text[:200]}")
            return []

        try:
            return [m.get("id") for m in response.json().get("data", []) if m.get("id")]
        except Exception as e:
            logger.error(f"Failed to parse the GitHub Copilot model list: {e}")
            return []
