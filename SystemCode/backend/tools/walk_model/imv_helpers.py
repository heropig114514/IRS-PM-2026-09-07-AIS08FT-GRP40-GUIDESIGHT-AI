# ruff: noqa: E501, E731, B023  (offline Kaggle experiment script, not runtime code)
"""HSV red/green check shared with the light-crop classifier study (same thresholds as train_light_crop.py)."""


def hsv_colour(img, box):
    x0, y0, x1, y1 = (int(round(v)) for v in box)
    crop = img.crop((max(0, x0), max(0, y0), max(x0 + 1, x1), max(y0 + 1, y1))).convert("HSV")
    red = green = 0
    for h, s, v in crop.getdata():
        if s < 90 or v < 120:
            continue
        if h < 15 or h > 235:
            red += 1
        elif 70 <= h <= 150:
            green += 1
    if red >= 3 and red >= 2 * green:
        return "red"
    if green >= 3 and green >= 2 * red:
        return "green"
    return "unknown"
