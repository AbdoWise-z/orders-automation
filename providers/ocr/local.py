import json
from pathlib import Path
from typing import Any

from paddleocr import PaddleOCRVL

from .base import OCRProvider
from .util import build_ocr_hierarchy


class LocalOCRProvider(OCRProvider):
    """Run PaddleOCR-VL locally."""

    def __init__(
        self,
        pipeline_version: str = "v1.6",
        device: str = "gpu:0",
    ):
        self.pipeline_version = pipeline_version
        self.device = device
        self._pipeline = None

    def _get_pipeline(self) -> PaddleOCRVL:
        if self._pipeline is None:
            self._pipeline = PaddleOCRVL(
                pipeline_version=self.pipeline_version,
                device=self.device,
            )

        return self._pipeline

    def extract(
        self,
        image_path: Path | str,
        output_dir: Path | str,
    ) -> tuple[str, dict[str, Any] | None]:
        image_path = str(image_path)

        pipeline = self._get_pipeline()
        output = pipeline.predict(image_path)

        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)

        results = list(output)

        for res in results:
            res.save_to_json(save_path=str(output_dir))
            res.save_to_markdown(save_path=str(output_dir))

        md_files = sorted(output_dir.glob("*.md"))
        json_files = sorted(output_dir.glob("*.json"))

        if not md_files or not json_files:
            raise RuntimeError(
                f"PaddleOCR-VL produced no Markdown output "
                f"for {image_path!r}"
            )

        return md_files[-1].read_text(encoding="utf-8"), build_ocr_hierarchy(json.loads(json_files[-1].read_text(encoding="utf-8")))