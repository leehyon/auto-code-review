import os
from typing import Dict, List, Optional, Union

from openai import OpenAI

from src.llm.client.base import BaseClient
from src.llm.types import NotGiven, NOT_GIVEN
from src.utils.log import logger


class CopilotClient(BaseClient):
    """GitHub Copilot client for chat models."""

    def __init__(self, api_key: str = None):
        self.api_key = api_key or os.getenv("GITHUB_COPILOT_TOKEN")
        self.base_url = "https://api.githubcopilot.com"
        if not self.api_key:
            raise ValueError(
                "GitHub Copilot token is required. "
                "Please provide GITHUB_COPILOT_TOKEN or set it in environment variables."
            )

        self.client = OpenAI(api_key=self.api_key, base_url=self.base_url)
        self.default_model = os.getenv("GITHUB_COPILOT_MODEL", "gpt-4o")

    def completions(
        self,
        messages: List[Dict[str, str]],
        model: Union[Optional[str], NotGiven] = NOT_GIVEN,
    ) -> str:
        model = model or self.default_model
        try:
            completion = self.client.chat.completions.create(
                model=model,
                messages=messages,
                extra_headers={
                    "OpenAI-Intent": "chat/completions",
                },
            )
            return completion.choices[0].message.content
        except Exception as e:
            logger.error(f"GitHub Copilot API error: {e}")
            raise