from __future__ import annotations

import os

from .base import OCRProvider
from .hf_private import HuggingFacePrivateOCRProvider
from .hf_public import HuggingFacePublicOCRProvider
from .local import LocalOCRProvider


def get_ocr_provider(
    provider: str | None = None,
) -> OCRProvider:
    provider = (
        provider
        or os.environ.get("OCR_PROVIDER")
        or "local"
    ).lower()

    if provider == "local":
        return LocalOCRProvider(
            pipeline_version="v1.6",
            device="cpu",
        )

    if provider in {"hf_public", "huggingface", "hf"}:
        return HuggingFacePublicOCRProvider()

    if provider in {"hf_private", "private"}:
        return HuggingFacePrivateOCRProvider()

    raise ValueError(
        f"Unknown OCR_PROVIDER {provider!r}. "
        "Choose one of: local, hf_public, hf_private."
    )