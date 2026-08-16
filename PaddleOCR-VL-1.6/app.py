from __future__ import annotations

import time
import traceback
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.concurrency import run_in_threadpool


# ---------------------------------------------------------------------------
# Pipeline
# ---------------------------------------------------------------------------

_pipeline = None


def get_pipeline():
    global _pipeline

    if _pipeline is None:
        from paddleocr import PaddleOCRVL

        print("=" * 60)
        print("Loading PaddleOCR-VL-1.6...")
        print("=" * 60)

        _pipeline = PaddleOCRVL(
            pipeline_version="v1.6",
            device="gpu:0",
        )

        print("=" * 60)
        print("PaddleOCR-VL-1.6 loaded successfully.")
        print("=" * 60)

    return _pipeline


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Load the model during startup so model loading is not included
    # in the first request's inference time. Run it in a worker thread
    # so a slow load doesn't block anything else during startup.
    await run_in_threadpool(get_pipeline)
    yield


# ---------------------------------------------------------------------------
# FastAPI
# ---------------------------------------------------------------------------

app = FastAPI(
    title="PaddleOCR-VL-1.6 API",
    description="PaddleOCR-VL-1.6 document OCR API",
    lifespan=lifespan,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def json_safe(value: Any) -> Any:
    """
    Convert Paddle/Numpy/etc. objects into JSON-serializable values.
    """
    try:
        import numpy as np

        if isinstance(value, np.ndarray):
            return value.tolist()

        if isinstance(value, np.generic):
            return value.item()
    except ImportError:
        pass

    if isinstance(value, dict):
        return {
            str(k): json_safe(v)
            for k, v in value.items()
        }

    if isinstance(value, (list, tuple)):
        return [json_safe(v) for v in value]

    if isinstance(value, Path):
        return str(value)

    if isinstance(value, (str, int, float, bool)) or value is None:
        return value

    return str(value)


def get_result_json(result: Any) -> Any:
    """
    Pull the structured result dict straight out of a PaddleOCR Result
    object's `.json` property. This is the same data `save_to_json()`
    would write to disk, but read from memory instead.
    """
    if hasattr(result, "json"):
        try:
            value = result.json

            if callable(value):
                value = value()

            return json_safe(value)
        except Exception:
            pass

    try:
        if isinstance(result, dict):
            return json_safe(result)
    except Exception:
        pass

    try:
        if hasattr(result, "__dict__"):
            return json_safe(result.__dict__)
    except Exception:
        pass

    return None


def get_markdown_text(result: Any) -> str | None:
    """
    Pull the markdown text straight out of a PaddleOCR Result object's
    `.markdown` property (a dict with a `markdown_texts` key). This is
    the same content `save_to_markdown()` would write to disk, but read
    from memory instead.
    """
    try:
        markdown = result.markdown
    except Exception:
        return None

    if isinstance(markdown, dict):
        text = markdown.get("markdown_texts")
        return text if isinstance(text, str) else None

    if isinstance(markdown, str):
        return markdown

    return None


# ---------------------------------------------------------------------------
# OCR processing (synchronous / blocking — always run via run_in_threadpool)
# ---------------------------------------------------------------------------

def predict_in_memory(pipeline: Any, contents: bytes) -> list[Any]:
    """
    Runs pipeline.predict() on a single image, decoded straight into a
    numpy array in memory — PaddleX pipelines accept numpy.ndarray input
    directly, so the upload never touches disk.
    """
    import cv2
    import numpy as np

    image_array = cv2.imdecode(
        np.frombuffer(contents, dtype=np.uint8),
        cv2.IMREAD_COLOR,
    )

    if image_array is None:
        raise ValueError("Could not decode the uploaded file as an image.")

    return list(pipeline.predict(image_array))


def run_ocr(pipeline: Any, contents: bytes) -> tuple[list[dict], str, float, int]:
    """
    Runs OCR end to end, entirely in memory. Does real GPU/CPU-bound
    work, so it must only ever be called through run_in_threadpool from
    the async route — never awaited or called directly on the event loop.
    """

    start = time.perf_counter()
    results = predict_in_memory(pipeline, contents)
    inference_time = time.perf_counter() - start

    print(f"OCR completed in {inference_time:.3f}s")

    pages = []
    markdown_parts = []

    for page_index, res in enumerate(results):
        raw_result = get_result_json(res)
        page_markdown = get_markdown_text(res)

        if page_markdown:
            markdown_parts.append(page_markdown)

        pages.append(
            {
                "page_index": page_index,
                "markdown": page_markdown,
                "raw_result": raw_result,
            }
        )

    markdown = "\n\n".join(part for part in markdown_parts if part)

    return pages, markdown, inference_time, len(results)


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.get("/")
def root():
    return {
        "status": "ok",
        "service": "PaddleOCR-VL-1.6",
    }


@app.get("/health")
def health():
    return {
        "status": "ok",
        "pipeline_loaded": _pipeline is not None,
    }


@app.post("/ocr")
async def ocr(file: UploadFile = File(...)):
    if not file.filename:
        raise HTTPException(
            status_code=400,
            detail="No filename provided.",
        )

    request_id = (
        datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        + "_"
        + uuid.uuid4().hex[:8]
    )

    contents = await file.read()

    try:
        pipeline = get_pipeline()

        print()
        print("=" * 60)
        print(f"OCR REQUEST: {request_id}")
        print(f"Input: {file.filename}")
        print(f"Size: {len(contents)} bytes")
        print("=" * 60)

        # Offload the blocking inference work to a worker thread so it
        # doesn't block the event loop (and therefore other requests,
        # including /health) for the whole duration of inference.
        pages, markdown, inference_time, page_count = await run_in_threadpool(
            run_ocr,
            pipeline,
            contents,
        )

        print(f"OCR REQUEST {request_id} done: {page_count} page(s)")
        print("=" * 60)

        return {
            "success": True,
            "request_id": request_id,
            "markdown": markdown,
            "inference_time_seconds": round(inference_time, 3),
            "page_count": page_count,
            "pages": pages,
            "debug": {
                "filename": file.filename,
                "content_type": file.content_type,
                "input_size_bytes": len(contents),
                "pipeline": {
                    "name": "PaddleOCR-VL",
                    "version": "1.6",
                    "device": "gpu:0",
                },
            },
        }

    except Exception as e:
        print()
        print("=" * 60)
        print(f"OCR FAILED: {request_id}")
        print("=" * 60)
        print(traceback.format_exc())
        print("=" * 60)

        raise HTTPException(
            status_code=500,
            detail={
                "success": False,
                "request_id": request_id,
                "message": str(e),
                "debug": {
                    "type": type(e).__name__,
                    "traceback": traceback.format_exc(),
                    "filename": file.filename,
                    "content_type": file.content_type,
                    "input_size_bytes": len(contents),
                },
            },
        )