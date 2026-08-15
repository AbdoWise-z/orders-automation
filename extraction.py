from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from providers import get_ocr_provider, get_llm_provider


def run_ocr(
    image_path: str,
    output_dir: str | Path,
    provider: str | None = None,
) -> tuple[str, dict[str, Any] | None]:
    """Run the configured OCR provider."""

    ocr_provider = get_ocr_provider(provider)

    return ocr_provider.extract(
        image_path=image_path,
        output_dir=output_dir,
    )


def extract_order_fields(
    markdown_text: str,
    ocr_json: dict[str, Any] | None = None,
    model: str | None = None,
    provider: str | None = None,
) -> dict:
    """Run structured extraction using the configured LLM provider."""

    llm_provider = get_llm_provider(provider)

    return llm_provider.extract(
        markdown_text=markdown_text,
        ocr_json=ocr_json,
        model=model,
    )