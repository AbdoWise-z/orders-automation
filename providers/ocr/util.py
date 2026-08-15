from __future__ import annotations

from typing import Any


def _bbox_area(bbox: list[float]) -> float:
    x1, y1, x2, y2 = bbox
    return max(0.0, x2 - x1) * max(0.0, y2 - y1)


def _intersection_area(
    a: list[float],
    b: list[float],
) -> float:
    x1 = max(a[0], b[0])
    y1 = max(a[1], b[1])
    x2 = min(a[2], b[2])
    y2 = min(a[3], b[3])

    if x2 <= x1 or y2 <= y1:
        return 0.0

    return (x2 - x1) * (y2 - y1)


def _contains(
    parent: list[float],
    child: list[float],
    tolerance: float = 2.0,
) -> bool:
    """
    Return True if child is spatially inside parent.

    A small tolerance is allowed because OCR/layout boxes are not
    always perfectly aligned.
    """

    return (
        child[0] >= parent[0] - tolerance
        and child[1] >= parent[1] - tolerance
        and child[2] <= parent[2] + tolerance
        and child[3] <= parent[3] + tolerance
    )


def _center_distance(
    a: list[float],
    b: list[float],
) -> float:
    ax = (a[0] + a[2]) / 2
    ay = (a[1] + a[3]) / 2

    bx = (b[0] + b[2]) / 2
    by = (b[1] + b[3]) / 2

    return ((ax - bx) ** 2 + (ay - by) ** 2) ** 0.5


def _vertical_gap(
    a: list[float],
    b: list[float],
) -> float:
    if a[3] < b[1]:
        return b[1] - a[3]

    if b[3] < a[1]:
        return a[1] - b[3]

    return 0.0


def _horizontal_gap(
    a: list[float],
    b: list[float],
) -> float:
    if a[2] < b[0]:
        return b[0] - a[2]

    if b[2] < a[0]:
        return a[0] - b[2]

    return 0.0


def _vertical_overlap(
    a: list[float],
    b: list[float],
) -> float:
    overlap = max(
        0.0,
        min(a[3], b[3]) - max(a[1], b[1]),
    )

    height = min(
        max(1.0, a[3] - a[1]),
        max(1.0, b[3] - b[1]),
    )

    return overlap / height


def _horizontal_overlap(
    a: list[float],
    b: list[float],
) -> float:
    overlap = max(
        0.0,
        min(a[2], b[2]) - max(a[0], b[0]),
    )

    width = min(
        max(1.0, a[2] - a[0]),
        max(1.0, b[2] - b[0]),
    )

    return overlap / width


def _merge_bbox(
    boxes: list[list[float]],
) -> list[float]:
    return [
        min(box[0] for box in boxes),
        min(box[1] for box in boxes),
        max(box[2] for box in boxes),
        max(box[3] for box in boxes),
    ]


def _make_node(
    *,
    node_type: str,
    bbox: list[float],
    text: str | None = None,
    label: str | None = None,
    children: list[dict[str, Any]] | None = None,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:

    node: dict[str, Any] = {
        "type": node_type,
        "bbox": bbox,
    }

    if label is not None:
        node["label"] = label

    if text is not None:
        node["text"] = text

    if metadata:
        node["metadata"] = metadata

    if children:
        node["children"] = children

    return node


def _strip_metadata(node: dict[str, Any]) -> dict[str, Any]:
    """
    Recursively remove metadata from a hierarchy tree.

    Keeps only the structural/spatial information useful to the LLM.
    """

    result: dict[str, Any] = {
        "type": node["type"],
        # "bbox": node["bbox"],
    }

    # if "label" in node:
    #     result["label"] = node["label"]

    if "text" in node:
        result["text"] = node["text"]

    if "children" in node:
        result["children"] = [
            _strip_metadata(child)
            for child in node["children"]
        ]

    return result

def build_ocr_hierarchy(
    original_json: dict[str, Any],
    minimalist: bool = True,
) -> dict[str, Any]:
    """
    Build a spatial hierarchy directly from PaddleOCR-VL's raw JSON.

    Args:
        original_json:
            Complete PaddleOCR-VL JSON response.

        minimalist:
            If True, remove all metadata from the hierarchy and keep
            only the structural/spatial information:

                type
                bbox
                label
                text
                children

            This significantly reduces the number of tokens sent to
            the extraction LLM.
    """

    res = original_json.get("res", original_json)

    parsing_blocks = res.get(
        "parsing_res_list",
        [],
    )

    layout_res = res.get(
        "layout_det_res",
        {},
    )

    layout_boxes = layout_res.get(
        "boxes",
        [],
    )

    blocks: list[dict[str, Any]] = []

    for index, block in enumerate(parsing_blocks):
        bbox = block.get("block_bbox")

        if not bbox or len(bbox) != 4:
            continue

        bbox = [float(x) for x in bbox]

        node = _make_node(
            node_type="block",
            bbox=bbox,
            text=block.get("block_content"),
            label=block.get("block_label"),
            metadata={
                "block_id": block.get("block_id"),
                "block_order": block.get("block_order"),
                "group_id": block.get("group_id"),
                "source_index": index,
            },
        )

        blocks.append(node)

    # --------------------------------------------------------------
    # Attach layout detections to containing semantic blocks.
    # --------------------------------------------------------------

    for layout_index, layout in enumerate(layout_boxes):
        coordinate = layout.get("coordinate")

        if not coordinate or len(coordinate) != 4:
            continue

        bbox = [float(x) for x in coordinate]

        best_block = None
        best_area = None

        for block in blocks:
            block_bbox = block["bbox"]

            if not _contains(
                block_bbox,
                bbox,
            ):
                continue

            area = _bbox_area(block_bbox)

            if best_area is None or area < best_area:
                best_area = area
                best_block = block

        layout_node = _make_node(
            node_type="layout",
            bbox=bbox,
            label=layout.get("label"),
            metadata={
                "cls_id": layout.get("cls_id"),
                "score": layout.get("score"),
                "order": layout.get("order"),
                "source_index": layout_index,
            },
        )

        if best_block is not None:
            best_block.setdefault(
                "children",
                [],
            ).append(layout_node)

    # --------------------------------------------------------------
    # Group blocks spatially.
    # --------------------------------------------------------------

    regions: list[list[dict[str, Any]]] = []

    sorted_blocks = sorted(
        blocks,
        key=lambda block: (
            block["bbox"][1],
            block["bbox"][0],
        ),
    )

    for block in sorted_blocks:
        best_region = None
        best_score = None

        for region in regions:
            region_bbox = _merge_bbox(
                [
                    child["bbox"]
                    for child in region
                ]
            )

            vertical_gap = _vertical_gap(
                region_bbox,
                block["bbox"],
            )

            horizontal_overlap = _horizontal_overlap(
                region_bbox,
                block["bbox"],
            )

            average_height = sum(
                max(
                    1.0,
                    child["bbox"][3] - child["bbox"][1],
                )
                for child in region
            ) / len(region)

            if (
                vertical_gap <= average_height * 2.5
                and horizontal_overlap >= 0.15
            ):
                score = (
                    vertical_gap
                    - horizontal_overlap * average_height
                )

                if best_score is None or score < best_score:
                    best_score = score
                    best_region = region

        if best_region is None:
            regions.append([block])
        else:
            best_region.append(block)

    # --------------------------------------------------------------
    # Convert regions to nodes.
    # --------------------------------------------------------------

    region_nodes = []

    for region_index, region in enumerate(regions):
        region_bbox = _merge_bbox(
            [
                block["bbox"]
                for block in region
            ]
        )

        region_node = _make_node(
            node_type="region",
            bbox=region_bbox,
            children=sorted(
                region,
                key=lambda block: (
                    block["bbox"][1],
                    block["bbox"][0],
                ),
            ),
            metadata={
                "region_index": region_index,
                "block_count": len(region),
            },
        )

        region_nodes.append(region_node)

    # --------------------------------------------------------------
    # Document root.
    # --------------------------------------------------------------

    width = res.get("width")
    height = res.get("height")

    if width is not None and height is not None:
        document_bbox = [
            0.0,
            0.0,
            float(width),
            float(height),
        ]
    elif blocks:
        document_bbox = _merge_bbox(
            [
                block["bbox"]
                for block in blocks
            ]
        )
    else:
        document_bbox = []

    hierarchy = _make_node(
        node_type="document",
        bbox=document_bbox,
        children=region_nodes,
        metadata={
            "width": width,
            "height": height,
            "block_count": len(blocks),
            "region_count": len(region_nodes),
        },
    )

    # --------------------------------------------------------------
    # Optional token-saving representation.
    # --------------------------------------------------------------

    if minimalist:
        return _strip_metadata(hierarchy)

    return hierarchy