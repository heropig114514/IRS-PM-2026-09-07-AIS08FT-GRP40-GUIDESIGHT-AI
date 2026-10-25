"""Responsibilities: Publish per-target coverage evidence and six truthful GT/prediction overlays.
Implementation: Explicit CSV tables,red/green geometry and labeled side panels; no rescoring.
Related Modules: unified_evaluation.diagnostics; original RGB and saved prediction artifacts.
Declaration Index: export_csv; overlay; person_crops; write_diagnostics.
Variable Index: RED; GREEN; GRAY — fixed requested GT/prediction annotation colors.
"""

import csv
import html
import json
import math
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from object_distance.metrics import bbox_iou

from .dataset import safe_path

RED = (220, 38, 38)
GREEN = (0, 160, 70)
GRAY = (78, 87, 98)


def export_csv(path, rows):
    """Functionality: Save independently inspectable tabular evidence including explicit nulls.
    Inputs: file path and dict rows. Outputs: UTF8 CSV. Logic: stable union of columns.
    Constraints: nested values use JSON,missing values use null rather than fabricated zero.
    """
    fields = list(dict.fromkeys(key for row in rows for key in row))
    with Path(path).open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            writer.writerow(
                {
                    key: json.dumps(value, allow_nan=False)
                    if isinstance(value, dict | list)
                    else value
                    if value is not None
                    else "null"
                    for key, value in row.items()
                }
            )


def overlay(image, frame, observations, predictions, output, index):
    """Functionality: Draw all original projected GT in red and saved native predictions in green.
    Inputs: original RGB,GT audit rows,predictions,owned output,index. Outputs: one labeled PNG.
    Logic: compact image IDs refer to complete class/confidence/IoU side-panel rows.
    Constraints: no excluded GT is hidden; GT confidence is not applicable,not a model score.
    """
    font = (
        ImageFont.truetype("C:/Windows/Fonts/arial.ttf", 15)
        if Path("C:/Windows/Fonts/arial.ttf").is_file()
        else ImageFont.load_default(size=15)
    )
    small = (
        ImageFont.truetype("C:/Windows/Fonts/arial.ttf", 13)
        if Path("C:/Windows/Fonts/arial.ttf").is_file()
        else ImageFont.load_default(size=13)
    )
    width, height = image.size
    panel, margin = 760, 76
    canvas = Image.new("RGB", (width + panel, height + margin + 60), "white")
    canvas.paste(image, (0, margin))
    draw = ImageDraw.Draw(canvas)
    draw.text(
        (16, 8),
        f"Frame {index:02d} | {frame['scene_id']} | CAM_FRONT | source {frame['timestamp']}",
        font=font,
        fill=(20, 30, 40),
    )
    draw.text(
        (16, 33),
        "RED = ALL original GT | GREEN = saved YOLO predictions | conf=0.25, IoU=0.50 unchanged",
        font=font,
        fill=GRAY,
    )
    for gi, row in enumerate(observations):
        x1, y1, x2, y2 = row["bbox"]
        draw.rectangle((x1, y1 + margin, x2, y2 + margin), outline=RED, width=2)
        text = f"G{gi + 1:02}"
        # Alternate compact IDs above/below dense far-object boxes; full details stay in the panel.
        y = max(margin, y1 + margin - 15 - (gi % 3) * 14)
        draw.text(
            (max(0, min(width - 40, x1)), y),
            text,
            font=small,
            fill=RED,
            stroke_width=1,
            stroke_fill="white",
        )
    for pi, prediction in enumerate(predictions):
        x1, y1, x2, y2 = prediction["bbox"]
        draw.rectangle((x1, y1 + margin, x2, y2 + margin), outline=GREEN, width=3)
        draw.text(
            (x1, min(height + margin - 18, y2 + margin + 2)),
            f"Y{pi + 1:02}",
            font=font,
            fill=GREEN,
            stroke_width=1,
            stroke_fill="white",
        )
    x, y = width + 15, margin
    draw.text(
        (x, y),
        "GT: class | confidence | best same-class IoU | z | input W x H | visibility | eligible",
        font=small,
        fill=RED,
    )
    y += 28
    for gi, row in enumerate(observations):
        status = "MATCH" if row["detection_matched"] else "unmatched"
        text = (
            f"G{gi + 1:02} {row['class_name']:<6} conf=GT/NA "
            f"IoU={row['best_kept_same_class_iou']:.3f} z={row['camera_z_m']:.1f}m "
            f"{row['input_width_px']:.1f}x{row['input_height_px']:.1f}px "
            f"v={row['visibility_token']} eligible={row['policy_eligible']} {status}"
        )
        draw.text((x, y), text, font=small, fill=RED)
        y += 24
    y += 18
    draw.text(
        (x, y),
        "YOLO: class | actual confidence | best same-class IoU | GT partner",
        font=small,
        fill=GREEN,
    )
    y += 28
    for pi, prediction in enumerate(predictions):
        scores = [
            (gi, bbox_iou(row["bbox"], prediction["bbox"]))
            for gi, row in enumerate(observations)
            if row["class_name"] == prediction["class_name"]
        ]
        best = max(scores, key=lambda value: value[1], default=None)
        partner = "none" if best is None else f"G{best[0] + 1:02}"
        iou = best[1] if best is not None else 0
        draw.text(
            (x, y),
            f"Y{pi + 1:02} {prediction['class_name']:<10} "
            f"conf={prediction['confidence']:.4f} IoU={iou:.3f} best={partner}",
            font=small,
            fill=GREEN,
        )
        y += 24
    draw.text(
        (16, height + margin + 14),
        "Eligibility is diagnostic only: every GT remains drawn and in the original denominator. "
        "Best IoU is class-aware; best partner need not be the final one-to-one assignment.",
        font=small,
        fill=GRAY,
    )
    canvas.save(output)


def person_crops(frames, observations, summary, output):
    """Functionality: Show every original person observation rather than cherry-picked examples.
    Inputs: ordered frames,all native audit rows,raw root,owned output. Outputs: contact sheet PNG.
    Logic: preserve image content,resize crops for inspection and label frame/GT evidence.
    Constraints: crop resizing is visualization only,never new detector input or evidence of truth.
    """
    people = [row for row in observations if row["class_name"] == "person"]
    columns, tile_width, tile_height = 8, 210, 205
    sheet = Image.new(
        "RGB", (columns * tile_width, math.ceil(len(people) / columns) * tile_height + 50), "white"
    )
    draw = ImageDraw.Draw(sheet)
    font = ImageFont.load_default(size=12)
    draw.text(
        (10, 8),
        "ALL person GT | crops enlarged for inspection only | RED GT | score/IoU unchanged",
        font=font,
        fill=GRAY,
    )
    images = {
        frame["frame_id"]: Image.open(safe_path(summary["raw_directory"], frame["image_path"]))
        for frame in frames
    }
    frame_indices = {frame["frame_id"]: index + 1 for index, frame in enumerate(frames)}
    try:
        for index, row in enumerate(people):
            image = images[row["frame_id"]]
            x1, y1, x2, y2 = row["bbox"]
            padding = max(6, (x2 - x1) * 0.3)
            region = [
                max(0, math.floor(x1 - padding)),
                max(0, math.floor(y1 - padding)),
                min(image.width, math.ceil(x2 + padding)),
                min(image.height, math.ceil(y2 + padding)),
            ]
            crop = image.crop(region)
            local = ImageDraw.Draw(crop)
            local.rectangle(
                (x1 - region[0], y1 - region[1], x2 - region[0], y2 - region[1]),
                outline=RED,
                width=1,
            )
            crop.thumbnail((200, 130))
            if crop.height < 130:
                factor = min(200 / crop.width, 130 / crop.height)
                crop = crop.resize(
                    (round(crop.width * factor), round(crop.height * factor)),
                    Image.Resampling.NEAREST,
                )
            x, y = (index % columns) * tile_width, 50 + (index // columns) * tile_height
            sheet.paste(crop, (x, y))
            color = GREEN if row["detection_matched"] else RED
            confidence = row["max_raw_confidence_at_required_iou"]
            score = "NA" if confidence is None else f"{confidence:.5f}"
            label = (
                f"#{index + 1} F{frame_indices[row['frame_id']]} "
                f"z={row['camera_z_m']:.1f}m v={row['visibility_token']}\n"
                f"input={row['input_width_px']:.1f}x{row['input_height_px']:.1f}px "
                f"IoU={row['best_kept_same_class_iou']:.2f}\n"
                f"raw conf@IoU>=.5: {score}\n"
                f"matched={row['detection_matched']} eligible={row['policy_eligible']}"
            )
            draw.multiline_text((x + 3, y + 134), label, font=font, fill=color, spacing=2)
    finally:
        for image in images.values():
            image.close()
    sheet.save(output)


def write_diagnostics(output, report, frames, geometry, methods, predictions, summary):
    """Functionality: Export complete GT/detection funnels,policy comparisons and visual evidence.
    Inputs: computed diagnosis,original frames/rows/predictions and raw summary. Outputs: files.
    Logic: retain all classes/reasons,original assignments and explicit subset denominators.
    Constraints: diagnostic eligibility does not update original metrics or hide difficult targets.
    """
    output = Path(output)
    overlays = output / "overlays"
    overlays.mkdir()
    export_csv(output / "raw-gt-annotations.csv", geometry)
    target_rows, coverage, funnels, losses, distributions, sensitivity, stages = (
        [],
        [],
        [],
        [],
        [],
        [],
        [],
    )
    for method, rows in methods.items():
        target_rows.extend({"method": method, **row} for row in rows)
        funnels.extend(
            {"method": method, **row} for row in report["methods"][method]["funnel_per_frame"]
        )
        groups = {
            "all": report["methods"][method]["overall"],
            **report["methods"][method]["classes"],
        }
        for name, scores in groups.items():
            coverage.append(
                {
                    "method": method,
                    "class": name,
                    **{
                        key: scores[key]
                        for key in (
                            "original_gt",
                            "matched",
                            "valid_distance",
                            "original_gt_coverage",
                            "policy_eligible_gt",
                            "policy_eligible_valid_distance",
                            "policy_eligible_coverage",
                        )
                    },
                }
            )
            losses.extend(
                {"method": method, "class": name, "reason": reason, "count": count}
                for reason, count in scores["unmatched_reasons"].items()
            )
            sensitivity.extend(
                {"method": method, "class": name, **row} for row in scores["sensitivity"]
            )
            for measure in (
                "distance_m",
                "input_height_px",
                "input_width_px",
                "input_area_px2",
                "unmatched_distance_m",
                "unmatched_input_height_px",
                "best_raw_aligned_confidence",
            ):
                distributions.extend(
                    {"method": method, "class": name, "measure": measure, **row}
                    for row in scores[measure]["histogram"]
                )
    for frame in frames:
        raw = [row for row in geometry if row["frame_id"] == frame["frame_id"]]
        for name in ("all", "car", "person"):
            selected = [
                row
                for row in raw
                if row["class_name"] is not None and (name == "all" or row["class_name"] == name)
            ]
            stages.append(
                {
                    "frame_id": frame["frame_id"],
                    "class": name,
                    "raw_all_categories": len(raw),
                    "raw_supported_gt": len(selected),
                    "positive_camera_z": sum(r["positive_camera_z"] for r in selected),
                    "beyond_near_plane": sum(r["beyond_near_plane"] for r in selected),
                    "projected_into_image": sum(r["bbox"] is not None for r in selected),
                    "original_evaluation_gt": sum(r["original_evaluation_gt"] for r in selected),
                    "policy_eligible_gt": sum(
                        r["policy_eligible"] and r["original_evaluation_gt"] for r in selected
                    ),
                }
            )
    for name, rows in (
        ("gt-stages.csv", stages),
        ("target-diagnostics.csv", target_rows),
        ("coverage.csv", coverage),
        ("detection-funnel.csv", funnels),
        ("unmatched-reasons.csv", losses),
        ("distributions.csv", distributions),
        ("visibility-size-sensitivity.csv", sensitivity),
    ):
        export_csv(output / name, rows)
    confidence_rows = []
    for stage, source in (
        ("raw_topk", report["raw_distributions"]),
        ("post_confidence", report["kept_confidence_distributions"]),
    ):
        for engine, classes in source.items():
            for category, scores in classes.items():
                confidence_rows.extend(
                    {"stage": stage, "engine": engine, "class": category, **row}
                    for row in scores["histogram"]
                )
    export_csv(output / "proposal-confidence-distributions.csv", confidence_rows)
    (output / "checks.json").write_text(
        json.dumps(
            {"geometry": report["geometry_checks"], "replay": report["replay_checks"]},
            indent=2,
            allow_nan=False,
        ),
        encoding="utf-8",
    )
    for index, frame in enumerate(frames, 1):
        with Image.open(safe_path(summary["raw_directory"], frame["image_path"])) as image:
            overlay(
                image,
                frame,
                [row for row in methods["a"] if row["frame_id"] == frame["frame_id"]],
                predictions["a"][frame["frame_id"]]["objects"],
                overlays / f"{index:02d}-{frame['frame_id']}.png",
                index,
            )
    person_crops(frames, methods["a"], summary, output / "all-person-crops.png")
    person = report["methods"]["a"]["classes"]["person"]
    rows = report["methods"]["a"]["overall"]
    people = [row for row in methods["a"] if row["class_name"] == "person"]
    small_people = sum(row["input_height_px"] < 32 for row in people)
    low_visibility = sum(row["visibility_token"] in {"1", "2"} for row in people)
    large_cases = [row for row in people if row["input_height_px"] >= 32]
    export_csv(output / "large-person-cases.csv", large_cases)
    missed_reasons = person["unmatched_reasons"]
    lines = [
        "# Fixed-standard coverage diagnosis",
        "",
        "Original model,weights,confidence0.25,IoU0.5 and original results are unchanged.",
        "",
        "## Main finding",
        "",
        "Multiple factors: weak detector support for small/low-confidence people coexists "
        "with partial visibility,truncation and projected cuboid annotations. "
        "No coordinate/projection or one-to-one assignment implementation error was identified.",
        "",
        f"Person: {small_people}/{len(people)} projected boxes have input height<32px; "
        f"{low_visibility}/{len(people)} have six-camera visibility token1/2. "
        "These groups overlap. "
        f"Even the declared eligible subset retains {person['policy_eligible_gt']} people but "
        f"only {person['policy_eligible_valid_distance']} have valid original matches/distances.",
        "",
        f"The original{len(people) - person['matched']} unmatched people have"
        f"{missed_reasons.get('same_class_iou_candidate_below_confidence', 0)} "
        "same-class IoU-qualified raw candidates below confidence,"
        f"{missed_reasons.get('only_raw_same_class_overlap_below_iou', 0)} "
        "only low-IoU raw overlaps and"
        f"{missed_reasons.get('no_same_class_overlap_in_raw_topk', 0)} no same-class raw overlap. "
        "These fixed-run counts diagnose detector output,not a proposed lower threshold.",
        "",
        "## Ground-truth stages",
        "",
        "```json",
        json.dumps(report["raw_gt_counts"], indent=2),
        "```",
        "",
        "Raw sample annotations include all directions/categories; original GT is the existing "
        "140-object camera-projected denominator.",
        "",
        "## Native detector funnel",
        "",
        "```json",
        json.dumps(
            {
                key: sum(frame[key] for frame in report["methods"]["a"]["funnel_per_frame"])
                for key in (
                    "raw_topk_rows",
                    "raw_valid_bbox_rows",
                    "confidence_passing_rows",
                    "confidence_passing_valid_boxes",
                    "supported_class_predictions",
                    "same_class_iou_candidate_pairs",
                    "one_to_one_matches",
                    "maximum_possible_matches",
                    "valid_distances",
                )
            },
            indent=2,
        ),
        "```",
        "",
        "Raw rows are e2e top300 outputs per frame; they are not300 confident detections "
        "or pre-head dense candidates.",
        "",
        "## Original and diagnostic-subset coverage",
        "",
        "| Method | Class | Original valid/GT | Original rate | Policy valid/GT | Policy rate |",
        "|---|---|---:|---:|---:|---:|",
    ]
    for row in coverage:
        original_rate = row["original_gt_coverage"]
        policy_rate = row["policy_eligible_coverage"]
        lines.append(
            f"| {row['method'].upper()} | {row['class']} | "
            f"{row['valid_distance']}/{row['original_gt']} | {original_rate:.2%} | "
            f"{row['policy_eligible_valid_distance']}/{row['policy_eligible_gt']} | "
            + (f"{policy_rate:.2%}" if policy_rate is not None else "null")
            + " |"
        )
    lines.extend(
        [
            "",
            "Policy is diagnostic-only: visibility token>=3 (six-camera visibility), "
            "retained bbox fraction>=0.5,input width>=8px,height>=16px. "
            "Size gates represent one/two stride8 cells,not guaranteed detectability.",
            "",
            "Policy statistics use ORIGINAL assignments without rematching. Every excluded GT "
            "remains in original coverage and in the red overlays."
            " Height sensitivity0/8/16/32px is reported together,not chosen to maximize coverage.",
            "",
            "## Person evidence",
            "",
            "```json",
            json.dumps(person, indent=2),
            "```",
            "",
            "## Attribution limits",
            "",
            "Geometry is checked against official get_sample_data,homogeneous transforms "
            "and official2D hull postprocessing. Matcher arithmetic and maximum assignment "
            "cardinality at the same threshold are audited. Inspect checks.json.",
            "",
            f"Unmatched native GT reasons: {json.dumps(rows['unmatched_reasons'])}.",
            "",
            "Low-confidence/IoU candidates are detector evidence,not proof of identity. "
            "Small images,low six-camera visibility and foreground overlap can coexist; "
            "these counts must not be added as disjoint causes.",
            "",
            "Foreground overlap is a conservative depth-ordered3D bbox proxy,not a mask. "
            "Six-camera visibility does not prove CAM_FRONT visibility.",
            "",
            "C's missing distances are separate from matching losses. "
            "No threshold experiment or model training is performed.",
            "",
        ]
    )
    (output / "report.md").write_text("\n".join(lines), encoding="utf-8")
    document = (
        '<!doctype html><html lang="en"><meta charset="utf-8">'
        "<title>Six-frame coverage diagnosis</title><style>"
        "body{font:15px/1.6 system-ui,sans-serif;max-width:1500px;margin:auto;"
        "padding:24px;color:#202a35}"
        "img{max-width:100%;height:auto}pre{white-space:pre-wrap}a{color:#246b9c}</style><body>"
    )
    document += (
        "<h1>Fixed-standard six-frame coverage diagnosis</h1>"
        "<p>Red: ALL original GT. Green: saved YOLO predictions. "
    )
    document += (
        "Full class,confidence and IoU details are in each image side panel. "
        "No hidden GT filtering.</p>"
    )
    document += (
        '<p><a href="diagnostic.json">Full diagnostic JSON</a> · '
        '<a href="report.md">Statistics and interpretation</a> · '
    )
    document += (
        '<a href="coverage.csv">Coverage CSV</a> · '
        '<a href="target-diagnostics.csv">Every original GT</a></p>'
    )
    document += (
        "<h2>Person summary</h2><pre>"
        + html.escape(
            json.dumps(
                {
                    key: person[key]
                    for key in (
                        "original_gt",
                        "matched",
                        "policy_eligible_gt",
                        "policy_eligible_valid_distance",
                        "visibility_token_counts",
                        "unmatched_reasons",
                        "policy_exclusion_counts_nonexclusive",
                    )
                },
                indent=2,
            )
        )
        + "</pre>"
    )
    for image in sorted(overlays.glob("*.png")):
        document += (
            f'<h2>{html.escape(image.stem)}</h2><a href="overlays/{image.name}">'
            f'<img src="overlays/{image.name}" alt="Red GT and green YOLO overlay"></a>'
        )
    document += (
        '<h2>All original person observations</h2><a href="all-person-crops.png">'
        '<img src="all-person-crops.png" alt="All person crops without selection">'
        "</a></body></html>"
    )
    (output / "index.html").write_text(document, encoding="utf-8")
