"""Responsibilities: Draw bounded export labels attached to their own detection boxes.
Implementation: Bounded local avoidance near the top edge; overlap is allowed when space runs out.
Related Modules: video_export.services; core.detector.label_font.
Declaration Index: overlap; label_text; layout_labels; annotate_frame.
Variable Index: COLORS - risk display colors; PRIORITY - urgent labels painted last on overlap.
"""

import io
import math

from PIL import Image, ImageDraw

from core.detector import label_font

COLORS = {"low": (0, 230, 130), "medium": (255, 220, 50), "high": (255, 145, 40),
          "critical": (255, 65, 65), "unknown": (190, 190, 190)}
PRIORITY = {"critical": 0, "high": 1, "medium": 2, "low": 3, "unknown": 4}


def overlap(a, b):
    """Functionality: Measure rectangle intersection. Inputs: Two xyxy bounds. Outputs: Area.
    Logic: Clip positive extents. Constraints: Label placement only, never detection suppression.
    """
    return max(0, min(a[2], b[2]) - max(a[0], b[0])) * max(
        0, min(a[3], b[3]) - max(a[1], b[1]))


def label_text(box, show_ids=False):
    """Functionality: Format class, filtered distance and current risk without debug clutter.
    Inputs: Box metadata and optional debug IDs. Outputs: Label text.
    Logic: Prefer display distance; unavailable is N/A, suspect depth adds ?, held risk adds HOLD.
    Constraints: No additional temporal filtering or changes to stored prediction values.
    """
    distance = box.get("display_distance_m", box.get("smoothed_distance_m"))
    if "display_distance_m" not in box and distance is None:
        distance = box.get("distance_m")
    known = type(distance) in (float, int) and math.isfinite(distance) and distance > 0
    meters = f"{distance:.1f}m" if known else "N/A"
    if box.get("distance_quality") == "outlier":
        meters += "?"
    label = box["label"] + (f" {box['light_state']}" if "light_state" in box else "")
    text = f"{label} | {meters} | {box['risk_level'].upper()}"
    if box.get("risk_data_status") == "held":
        text += " [HOLD]"
    if show_ids and box.get("track_id") is not None:
        text = f"#{box['track_id']} {text}"
    return text


def layout_labels(image, boxes, show_ids=False):
    """Functionality: Reduce label overlap without moving labels far from their own boxes.
    Inputs: RGB image, boxes and debug-ID option. Outputs: Placed (box,text,rect,font) tuples.
    Logic: Try the anchor and four nearby offsets; minimize overlap, then movement. Allocate in
    track order independent of risk, and paint urgent labels last. Shorten long class text to fit.
    Constraints: Offsets are capped at 28px and connectors at 30px in inference coordinates.
    Overlap is allowed when local space runs out; no free-row search or opposite-edge relocation.
    Very small images may not fit the required distance/risk text; boxes remain drawn regardless.
    """
    draw, font, placed = ImageDraw.Draw(image), label_font(), []
    ordered = sorted(boxes, key=lambda b: (b.get("track_id") or 0,
                     b["label"], tuple(b["xyxy"])), reverse=True)
    for box in ordered:
        text = label_text(box, show_ids)
        label, suffix = text.split(" | ", 1)
        while draw.textbbox((0, 0), text, font=font)[2] + 8 > image.width and len(label) > 1:
            label = label[:-1]
            text = f"{label}… | {suffix}"
        bounds = draw.textbbox((0, 0), text, font=font)
        width, height = bounds[2] - bounds[0] + 8, bounds[3] - bounds[1] + 8
        if width > image.width or height > image.height:
            continue
        x1, y1 = box["xyxy"][:2]
        left = max(0, min(x1, image.width - width))
        top = max(0, min(y1 - height - 2, image.height - height))
        rect = (left, top, left + width, top + height)
        candidates = [rect]
        step = min(height + 2, 28)
        for dx, dy in ((0, -step), (0, step), (-24, 0), (24, 0)):
            x = max(0, min(left + dx, image.width - width))
            y = max(0, min(top + dy, image.height - height))
            candidate = (x, y, x + width, y + height)
            tether = math.hypot(max(x - x1, x1 - candidate[2], 0),
                                max(y - y1, y1 - candidate[3], 0))
            if tether <= 30:
                candidates.append(candidate)
        rect = min(candidates, key=lambda r: (
            sum(overlap(r, previous[2]) for previous in placed),
            math.hypot(r[0] - left, r[1] - top)))
        placed.append((box, text, rect, font))
    return sorted(placed, key=lambda item: PRIORITY[item[0]["risk_level"]], reverse=True)


def annotate_frame(jpeg, metadata, show_ids=False):
    """Functionality: Render boxes, stable meters and risk with box-attached labels.
    Inputs: Exact inference JPEG, metadata, optional debug IDs. Outputs: Annotated RGB image.
    Logic: Draw boxes, short top-edge connectors, then labels with urgent labels on top.
    Constraints: Coordinates share the inference frame; only layout changes image content.
    """
    with Image.open(io.BytesIO(jpeg)) as original:
        image = original.convert("RGB")
    draw = ImageDraw.Draw(image)
    for box in metadata["boxes"]:
        draw.rectangle(box["xyxy"], outline=COLORS[box["risk_level"]], width=2)
    labels = layout_labels(image, metadata["boxes"], show_ids)
    for box, _, rect, _ in labels:
        x, y = box["xyxy"][:2]
        target = (max(rect[0], min(x, rect[2])), max(rect[1], min(y, rect[3])))
        draw.line([(x, y), target], fill=COLORS[box["risk_level"]], width=1)
    for box, text, rect, font in labels:
        draw.rectangle(rect, fill=(15, 25, 30))
        bounds = draw.textbbox((0, 0), text, font=font)
        draw.text((rect[0] + 4, rect[1] + 4 - bounds[1]), text, font=font,
                  fill=COLORS[box["risk_level"]])
    return image
