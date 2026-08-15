from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from .base import OCRProvider


DEFAULT_SPACE = "PaddlePaddle/PaddleOCR-VL-1.6_Online_Demo"


class HuggingFacePublicOCRProvider(OCRProvider):
    """Run PaddleOCR-VL using the public Hugging Face Space."""

    def __init__(
        self,
        space: str | None = None,
        token: str | None = None,
    ):
        self.space = (
            space
            or os.environ.get("HF_OCR_SPACE")
            or DEFAULT_SPACE
        )

        self.token = token or os.environ.get("HF_TOKEN")

        self._client = None

    def _get_client(self):
        if self._client is None:
            from gradio_client import Client

            if self.token:
                self._client = Client(
                    self.space,
                    token=self.token,
                )
            else:
                self._client = Client(self.space)

        return self._client

    def extract(
        self,
        image_path: str | Path,
        output_dir: str | Path,
    ) -> tuple[str, dict[str, Any] | None]:
        image_path = Path(image_path)
        output_dir = Path(output_dir)

        output_dir.mkdir(parents=True, exist_ok=True)

        client = self._get_client()

        from gradio_client import handle_file

        try:
            result = client.predict(
                handle_file(str(image_path)),
                False,  # chart parsing
                False,  # document unwarping
                False,  # orientation classification
                api_name="/parse_doc",
            )
        except Exception as exc:
            raise RuntimeError(
                "Public Hugging Face PaddleOCR-VL request failed. "
                f"Space={self.space!r}. Original error: {exc}"
            ) from exc

        if not isinstance(result, (tuple, list)):
            raise RuntimeError(
                "Unexpected response from the public PaddleOCR-VL "
                f"Space: {result!r}"
            )

        if len(result) < 3:
            raise RuntimeError(
                "Public PaddleOCR-VL Space returned fewer than "
                f"3 outputs: {result!r}"
            )

        # /parse_doc:
        #
        # result[0] = Markdown preview
        # result[1] = visualization HTML
        # result[2] = raw Markdown

        markdown_text = result[2] or result[0]

        if not markdown_text:
            raise RuntimeError(
                "Public Hugging Face PaddleOCR-VL produced no Markdown."
            )

        markdown_text = str(markdown_text)

        md_path = output_dir / f"{image_path.stem}.md"
        md_path.write_text(
            markdown_text,
            encoding="utf-8",
        )

        audit_path = output_dir / f"{image_path.stem}.json"
        audit_path.write_text(
            json.dumps(
                {
                    "ocr_provider": "hf_public",
                    "huggingface_space": self.space,
                    "source_image": str(image_path),
                    "markdown_file": md_path.name,
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )

        return markdown_text, None