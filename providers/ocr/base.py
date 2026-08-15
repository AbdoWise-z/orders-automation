from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any


class OCRProvider(ABC):
    """Base interface for all OCR providers."""

    @abstractmethod
    def extract(
        self,
        image_path: Path | str,
        output_dir: Path | str,
    ) -> tuple[str, dict[str, Any] | None]:
        """
        Extract OCR/document content from an image.

        Returns:
            Markdown representation of the document.
        """
        raise NotImplementedError