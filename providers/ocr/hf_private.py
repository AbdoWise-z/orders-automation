from __future__ import annotations

import json
import os
import shutil
from pathlib import Path
from typing import Any

import requests

from .base import OCRProvider
from .util import build_ocr_hierarchy


class HuggingFacePrivateOCRProvider(OCRProvider):
    """
    Run PaddleOCR-VL through the user's private FastAPI service.

    The image is uploaded to the FastAPI server as multipart/form-data.
    """

    def __init__(
        self,
        base_url: str | None = None,
        endpoint: str | None = None,
        api_key: str | None = None,
        timeout: int = 600,
    ):
        self.base_url = (
            base_url
            or os.environ.get("HF_PRIVATE_OCR_URL")
            or ""
        ).rstrip("/")

        self.endpoint = (
            endpoint
            or os.environ.get("HF_PRIVATE_OCR_ENDPOINT")
            or "/ocr"
        )

        self.api_key = (
            api_key
            or os.environ.get("HF_PRIVATE_OCR_API_KEY")
        )

        self.timeout = timeout

        if not self.base_url:
            raise ValueError(
                "HF_PRIVATE_OCR_URL is required for the "
                "'hf_private' OCR provider."
            )

    def extract(
        self,
        image_path: str | Path,
        output_dir: str | Path,
    ) -> tuple[str, dict[str, Any]]:
        image_path = Path(image_path)
        output_dir = Path(output_dir)

        output_dir.mkdir(parents=True, exist_ok=True)

        url = f"{self.base_url}{self.endpoint}"

        headers = {}

        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"

        # --------------------------------------------------------------
        # Preserve the exact source image used for this OCR run.
        # --------------------------------------------------------------

        debug_input_path = (
            output_dir / f"{image_path.stem}_input{image_path.suffix}"
        )

        if image_path.resolve() != debug_input_path.resolve():
            shutil.copy2(
                image_path,
                debug_input_path,
            )

        # --------------------------------------------------------------
        # Call private FastAPI service.
        # --------------------------------------------------------------

        try:
            with image_path.open("rb") as image_file:
                response = requests.post(
                    url,
                    headers=headers,
                    files={
                        "file": (
                            image_path.name,
                            image_file,
                            "application/octet-stream",
                        )
                    },
                    timeout=self.timeout,
                )
        except requests.RequestException as exc:
            raise RuntimeError(
                "Private Hugging Face OCR API request failed: "
                f"{exc}"
            ) from exc

        # --------------------------------------------------------------
        # Save raw HTTP response/status for debugging.
        # --------------------------------------------------------------

        status_path = (
            output_dir / f"{image_path.stem}_http_status.txt"
        )
        status_path.write_text(
            str(response.status_code),
            encoding="utf-8",
        )

        raw_response_path = (
            output_dir / f"{image_path.stem}_raw_response.txt"
        )
        raw_response_path.write_text(
            response.text,
            encoding="utf-8",
        )

        if not response.ok:
            raise RuntimeError(
                "Private Hugging Face OCR API returned "
                f"HTTP {response.status_code}: {response.text}"
            )

        # --------------------------------------------------------------
        # Parse response.
        # --------------------------------------------------------------

        try:
            data: dict[str, Any] = response.json()
        except ValueError as exc:
            raise RuntimeError(
                "Private Hugging Face OCR API did not return JSON. "
                f"Response: {response.text[:1000]}"
            ) from exc

        # --------------------------------------------------------------
        # Save complete API response.
        # --------------------------------------------------------------

        response_json_path = (
            output_dir / f"{image_path.stem}_response.json"
        )

        response_json_path.write_text(
            json.dumps(
                data,
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )

        # --------------------------------------------------------------
        # Markdown
        # --------------------------------------------------------------

        markdown_text = data.get("markdown")

        if not markdown_text:
            raise RuntimeError(
                "Private Hugging Face OCR API response does not "
                "contain a non-empty 'markdown' field: "
                f"{data!r}"
            )

        markdown_text = str(markdown_text)

        md_path = output_dir / f"{image_path.stem}.md"

        md_path.write_text(
            markdown_text,
            encoding="utf-8",
        )

        # --------------------------------------------------------------
        # Clean spatial OCR JSON.
        #
        # The API returns the raw PaddleOCR result in each page.
        # We reduce it to the information useful to the LLM:
        #
        #   - text/content
        #   - bounding boxes / polygons
        #   - ordering
        #   - grouping
        #   - labels
        #   - confidence
        #
        # This intentionally preserves spatial information.
        # --------------------------------------------------------------

        pages = data.get("pages", [])

        cleaned_ocr: dict[str, Any] = {
            "pages": [],
        }

        pages_dir = (
            output_dir / f"{image_path.stem}_pages"
        )
        pages_dir.mkdir(
            parents=True,
            exist_ok=True,
        )

        for page in pages:
            page_index = page.get("page_index", 0)

            page_dir = pages_dir / f"page_{page_index}"
            page_dir.mkdir(
                parents=True,
                exist_ok=True,
            )

            raw_result = page.get("raw_result")

            if raw_result is None:
                continue

            # Preserve raw result for debugging.
            (page_dir / "raw_result.json").write_text(
                json.dumps(
                    raw_result,
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )

            # Clean it for the extraction LLM.
            cleaned_page = build_ocr_hierarchy(raw_result)

            cleaned_ocr["pages"].append(
                {
                    "page": page_index + 1,
                    **cleaned_page,
                }
            )

            # Preserve page Markdown for debugging.
            page_markdown = page.get("markdown")

            if page_markdown is not None:
                (page_dir / "markdown.md").write_text(
                    str(page_markdown),
                    encoding="utf-8",
                )

        # --------------------------------------------------------------
        # Save the cleaned OCR representation separately.
        # --------------------------------------------------------------

        cleaned_json_path = (
            output_dir / f"{image_path.stem}_cleaned_ocr.json"
        )

        cleaned_json_path.write_text(
            json.dumps(
                cleaned_ocr,
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )

        # --------------------------------------------------------------
        # Save timing/debug metadata.
        # --------------------------------------------------------------

        audit_path = (
            output_dir / f"{image_path.stem}.json"
        )

        audit_path.write_text(
            json.dumps(
                {
                    "ocr_provider": "hf_private",
                    "api_url": url,
                    "source_image": str(image_path),
                    "debug_input_image": debug_input_path.name,
                    "markdown_file": md_path.name,
                    "response_file": response_json_path.name,
                    "cleaned_ocr_file": cleaned_json_path.name,
                    "pages_directory": pages_dir.name,
                    "success": data.get("success"),
                    "request_id": data.get("request_id"),
                    "inference_time_seconds": data.get(
                        "inference_time_seconds"
                    ),
                    "page_count": data.get(
                        "page_count"
                    ),
                    "remote_debug": data.get("debug", {}),
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )

        return markdown_text, cleaned_ocr