from __future__ import annotations

import json
import os
from typing import Any

from .base import LLMProvider
from .prompt import ANTHROPIC_TOOL, TOOL_NAME, EXTRACTION_SYSTEM_PROMPT


class AnthropicLLMProvider(LLMProvider):

    def __init__(
        self,
        default_model: str = "claude-haiku-4-5-20251001",
    ):
        self.default_model = default_model
        self._client = None

    def _get_client(self):
        if self._client is None:
            import anthropic
            self._client = anthropic.Anthropic()

        return self._client

    def extract(
        self,
        markdown_text: str,
        ocr_json: dict[str, Any] | None = None,
        model: str | None = None,
        provider: str | None = None,
    ) -> dict:
        model = model or os.environ.get("ANTHROPIC_LLM", self.default_model)

        client = self._get_client()

        message = client.messages.create(
            model=model,
            max_tokens=4096,
            system=EXTRACTION_SYSTEM_PROMPT,
            tools=[ANTHROPIC_TOOL],
            tool_choice={
                "type": "tool",
                "name": TOOL_NAME,
            },
            messages=[
                {
                    "role": "user",
                    "content": (
                        "Parsed source document (Markdown):\n\n"
                        f"{markdown_text}\n\n"
                        "Spatial OCR data (JSON):\n\n"
                        f"{json.dumps(ocr_json or {}, ensure_ascii=False, indent=2)}"
                    ),
                }
            ],
        )

        for block in message.content:
            if block.type == "tool_use":
                return block.input

        raise RuntimeError(
            "Anthropic response did not include the expected tool call"
        )