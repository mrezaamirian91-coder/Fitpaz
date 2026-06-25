"""رسم تگ کالری روی عکس غذا — واو مومنت بصری."""

import io
import logging
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

logger = logging.getLogger(__name__)

FONT_CANDIDATES = [
    Path(__file__).parent / "fonts" / "Vazirmatn-Regular.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "C:/Windows/Fonts/arial.ttf",
    "C:/Windows/Fonts/tahoma.ttf",
]


def _load_font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    for path in FONT_CANDIDATES:
        try:
            if Path(path).is_file():
                return ImageFont.truetype(str(path), size=size)
        except OSError:
            continue
    return ImageFont.load_default()


def _valid_box(box, width: int, height: int) -> tuple[int, int, int, int] | None:
    if not box or len(box) != 4:
        return None
    try:
        ymin, xmin, ymax, xmax = [float(v) for v in box]
    except (TypeError, ValueError):
        return None

    if max(ymin, xmin, ymax, xmax) <= 1:
        ymin, xmin, ymax, xmax = ymin * 1000, xmin * 1000, ymax * 1000, xmax * 1000

    if ymax <= ymin or xmax <= xmin:
        return None

    x1 = int(xmin / 1000 * width)
    y1 = int(ymin / 1000 * height)
    x2 = int(xmax / 1000 * width)
    y2 = int(ymax / 1000 * height)

    x1 = max(0, min(x1, width - 1))
    x2 = max(0, min(x2, width - 1))
    y1 = max(0, min(y1, height - 1))
    y2 = max(0, min(y2, height - 1))

    if x2 - x1 < 8 or y2 - y1 < 8:
        return None
    return x1, y1, x2, y2


def _truncate_name(name: str, max_len: int = 14) -> str:
    name = (name or "").strip()
    if len(name) <= max_len:
        return name
    return name[: max_len - 1] + "…"


def create_tagged_image(image_bytes: bytes, items: list) -> tuple[bytes, list]:
    """تگ روی عکس می‌زند. خروجی: (bytes تصویر JPEG, آیتم‌هایی که تگ نشدند)."""
    img = Image.open(io.BytesIO(image_bytes)).convert("RGB")
    draw = ImageDraw.Draw(img)
    width, height = img.size

    font_label = _load_font(max(14, width // 45))
    font_small = _load_font(max(11, width // 55))
    untagged = []

    for item in items:
        box = _valid_box(item.get("box_2d"), width, height)
        name = _truncate_name(item.get("name", ""))
        calories = item.get("calories", 0)
        label = f"{name}\n{calories} kcal" if name else f"{calories} kcal"

        if box is None:
            untagged.append(item)
            continue

        x1, y1, x2, y2 = box
        draw.rectangle([x1, y1, x2, y2], outline=(255, 87, 34), width=max(2, width // 300))

        text_x = x1 + 4
        text_y = max(4, y1 - 36)
        bbox = draw.textbbox((text_x, text_y), label, font=font_label)
        pad = 4
        draw.rectangle(
            [bbox[0] - pad, bbox[1] - pad, bbox[2] + pad, bbox[3] + pad],
            fill=(0, 0, 0, 200),
        )
        draw.text((text_x, text_y), label, fill=(255, 255, 255), font=font_label)

    out = io.BytesIO()
    img.save(out, format="JPEG", quality=90)
    return out.getvalue(), untagged


def format_untagged_lines(items: list) -> str:
    if not items:
        return ""
    lines = ["📋 *جزئیات:*"]
    for item in items:
        qty = f" ({item['quantity']})" if item.get("quantity") else ""
        macros = ""
        if item.get("protein_g") or item.get("carbs_g") or item.get("fat_g"):
            macros = (
                f" | پروتئین {item.get('protein_g', 0)}گ"
                f" | کربو {item.get('carbs_g', 0)}گ"
                f" | چربی {item.get('fat_g', 0)}گ"
            )
        lines.append(f"• {item.get('name', '')}{qty}: *{item.get('calories', 0)}* کالری{macros}")
    return "\n".join(lines)
