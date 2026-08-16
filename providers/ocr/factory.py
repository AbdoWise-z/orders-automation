from __future__ import annotations

import os

from .base import OCRProvider
from .hf_private import HuggingFacePrivateOCRProvider
from .hf_public import HuggingFacePublicOCRProvider
# from .local import LocalOCRProvider


def get_ocr_provider(
    provider: str | None = None,
) -> OCRProvider:
    # Defaults to the hosted public Space: 'local' needs PaddleOCR's full
    # (very large) runtime, so it stays opt-in and the out-of-the-box path
    # requires no heavyweight install.
    provider = (
        provider
        or os.environ.get("OCR_PROVIDER")
        or "hf_public"
    ).lower()

    # if provider == "local":
    #     return LocalOCRProvider(
    #         pipeline_version="v1.6",
    #         device="cpu",
    #     )

    if provider in {"hf_public", "huggingface", "hf"}:
        return HuggingFacePublicOCRProvider()

    if provider in {"hf_private", "private"}:
        return HuggingFacePrivateOCRProvider()

    if provider == "local":
        raise ValueError(
            "OCR_PROVIDER='local' is disabled: it needs PaddleOCR's local "
            "runtime, which is a very large install. Uncomment LocalOCRProvider "
            "in providers/ocr/{__init__,factory}.py and install paddleocr to "
            "enable it. Otherwise use hf_public or hf_private."
        )
    raise ValueError(
        f"Unknown OCR_PROVIDER {provider!r}. Choose hf_public or hf_private "
        f"(or enable 'local' — see providers/ocr/factory.py)."
    )