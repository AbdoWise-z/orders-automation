from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class LLMProvider(ABC):
    """Interface implemented by every LLM extraction backend."""

    @abstractmethod
    def extract(
        self,
        markdown_text: str,
        ocr_json: dict[str, Any] | None = None,
        model: str | None = None,
        provider: str | None = None,
    ) -> dict:
        raise NotImplementedError