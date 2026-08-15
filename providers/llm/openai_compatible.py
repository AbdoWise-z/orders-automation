from __future__ import annotations

import json
import os
from typing import Any

from .prompt import OPENAI_TOOL, TOOL_NAME, EXTRACTION_SYSTEM_PROMPT

from .base import LLMProvider

class OpenAICompatibleLLMProvider(LLMProvider):

    def __init__(
        self,
        provider_name: str,
        default_model: str,
        base_url: str | None,
        api_key_env: str | None,
    ):
        self.provider_name = provider_name
        self.default_model = default_model
        self.base_url = base_url
        self.api_key_env = api_key_env

        self._client = None

    def _get_client(self):
        if self._client is None:
            from openai import OpenAI

            if self.api_key_env:
                api_key = os.environ.get(self.api_key_env)

                if not api_key:
                    raise RuntimeError(
                        f"{self.api_key_env} is not set."
                    )
            else:
                api_key = "not-needed"

            self._client = OpenAI(
                base_url=self.base_url,
                api_key=api_key,
            )

        return self._client

    def extract(
        self,
        markdown_text: str,
        ocr_json: dict[str, Any] | None = None,
        model: str | None = None,
        provider: str | None = None,
    ) -> dict:
        model = model or self.default_model

        client = self._get_client()

        response = client.chat.completions.create(
            model=model,
            max_completion_tokens=4096,
            tools=[OPENAI_TOOL],
            tool_choice={
                "type": "function",
                "function": {
                    "name": TOOL_NAME,
                },
            },
            messages=[
                {
                    "role": "system",
                    "content": EXTRACTION_SYSTEM_PROMPT,
                },
                {
                    "role": "user",
                    "content": (
                        "Parsed source document (Markdown):\n\n"
                        f"{markdown_text}\n\n"
                        "Spatial OCR data (JSON):\n\n"
                        f"{json.dumps(ocr_json or {}, ensure_ascii=False, indent=2)}"
                    ),
                },
            ],
        )

        tool_calls = (
                response.choices[0].message.tool_calls
                or []
        )

        for call in tool_calls:
            if call.function.name == TOOL_NAME:
                return json.loads(call.function.arguments)

        raise RuntimeError(
            f"{self.provider_name} response did not include the expected tool call"
        )