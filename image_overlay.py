"""رسم کارت واو مومنت — استایل A: تگ سفید + hero پایین + برند."""

import io
import logging
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

logger = logging.getLogger(__name__)

MAX_ON_IMAGE_TAGS = 5
BRAND_TEXT = "فیت پز"
MIN_IMAGE_WIDTH = 1200

FONT_BOLD_CANDIDATES = [
    Path(__file__).parent / "fonts" / "Vazirmatn-Bold.ttf",
    "C:/Windows/Fonts/tahomabd.ttf",
    "C:/Windows/Fonts/arialbd.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
]

FONT_REGULAR_CANDIDATES = [
    Path(__file__).parent / "fonts" / "Vazirmatn-Regular.ttf",
    "C:/Windows/Fonts/tahoma.ttf",
    "C:/Windows/Fonts/arial.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
]


def _load_font(candidates: list, size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    for path in candidates:
        try:
            if Path(path).is_file():
                return ImageFont.truetype(str(path), size=size)
        except OSError:
            continue
    return ImageFont.load_default()


def _prepare_image(image_bytes: bytes) -> Image.Image:
    """عکس‌های کوچک تلگرام را بزرگ می‌کند تا تگ‌ها خوانا بمانند."""
    img = Image.open(io.BytesIO(image_bytes)).convert("RGB")
    width, height = img.size
    if width < MIN_IMAGE_WIDTH:
        scale = MIN_IMAGE_WIDTH / width
        img = img.resize((MIN_IMAGE_WIDTH, int(height * scale)), Image.Resampling.LANCZOS)
    return img


def _scale_sizes(width: int) -> dict:
    return {
        "tag": max(56, width // 9),
        "hero": max(80, width // 4),
        "macro": max(26, width // 16),
        "brand": max(22, width // 18),
        "pad_x": max(20, width // 20),
        "pad_y": max(14, width // 28),
        "radius": max(16, width // 30),
        "leader": max(4, width // 180),
        "dot_r": max(8, width // 70),
    }


def _to_fa_digits(value) -> str:
    return str(value).translate(str.maketrans("0123456789", "۰۱۲۳۴۵۶۷۸۹"))


def _box_center(box, width: int, height: int) -> tuple[int, int] | None:
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

    cx = int((xmin + xmax) / 2 / 1000 * width)
    cy = int((ymin + ymax) / 2 / 1000 * height)
    return max(0, min(cx, width - 1)), max(0, min(cy, height - 1))


def _rounded_rect(draw: ImageDraw.ImageDraw, xy, radius: int, fill, outline=None, width: int = 1):
    draw.rounded_rectangle(xy, radius=radius, fill=fill, outline=outline, width=width)


def _text_size(draw: ImageDraw.ImageDraw, text: str, font) -> tuple[int, int]:
    bbox = draw.textbbox((0, 0), text, font=font)
    return bbox[2] - bbox[0], bbox[3] - bbox[1]


def _rects_overlap(a, b, margin: int = 8) -> bool:
    return not (a[2] + margin < b[0] or a[0] - margin > b[2] or a[3] + margin < b[1] or a[1] - margin > b[3])


def _place_tag(
    center: tuple[int, int],
    tag_w: int,
    tag_h: int,
    width: int,
    height: int,
    hero_h: int,
    placed: list,
    index: int,
) -> tuple[int, int] | None:
    cx, cy = center
    directions = [
        (1, -1), (-1, -1), (1, 1), (-1, 1),
        (1, 0), (-1, 0), (0, -1), (0, 1),
    ]
    offset = max(52, width // 10) + (index % 3) * 16

    for dx, dy in directions:
        tx = cx + dx * offset
        ty = cy + dy * offset
        tx = max(12, min(tx, width - tag_w - 12))
        ty = max(12, min(ty, height - hero_h - tag_h - 12))
        rect = (tx, ty, tx + tag_w, ty + tag_h)
        if not any(_rects_overlap(rect, p) for p in placed):
            return tx, ty
    return None


def _draw_leader(draw: ImageDraw.ImageDraw, start: tuple[int, int], end: tuple[int, int], sizes: dict):
    line_w = sizes["leader"]
    outline_w = line_w + 3
    draw.line([start, end], fill=(0, 0, 0), width=outline_w)
    draw.line([start, end], fill=(255, 255, 255), width=line_w)

    r = sizes["dot_r"]
    draw.ellipse(
        [start[0] - r, start[1] - r, start[0] + r, start[1] + r],
        fill=(255, 255, 255),
        outline=(0, 0, 0),
        width=max(2, line_w),
    )


def _draw_pill_tag(draw: ImageDraw.ImageDraw, xy, text: str, font, sizes: dict):
    x, y = xy
    tw, th = _text_size(draw, text, font)
    pad_x = sizes["pad_x"]
    pad_y = sizes["pad_y"]
    rect = (x, y, x + tw + pad_x * 2, y + th + pad_y * 2)
    radius = sizes["radius"]

    shadow = (rect[0] + 4, rect[1] + 5, rect[2] + 4, rect[3] + 5)
    _rounded_rect(draw, shadow, radius, fill=(40, 40, 40))
    _rounded_rect(draw, rect, radius, fill=(255, 255, 255), outline=(200, 200, 200), width=2)
    draw.text((x + pad_x, y + pad_y), text, fill=(15, 15, 15), font=font)
    return rect


def _draw_hero_bar(img: Image.Image, totals: dict):
    width, height = img.size
    sizes = _scale_sizes(width)
    hero_h = max(int(height * 0.26), int(width * 0.22))
    hero_h = min(hero_h, int(height * 0.36))

    overlay = Image.new("RGBA", (width, hero_h), (0, 0, 0, 0))
    pixels = overlay.load()
    for row in range(hero_h):
        t = row / max(hero_h - 1, 1)
        alpha = int(50 + 190 * t)
        for col in range(width):
            pixels[col, row] = (0, 0, 0, alpha)

    base = img.convert("RGBA")
    base.paste(overlay, (0, height - hero_h), overlay)
    draw = ImageDraw.Draw(base)

    font_hero = _load_font(FONT_BOLD_CANDIDATES, sizes["hero"])
    font_macro = _load_font(FONT_REGULAR_CANDIDATES, sizes["macro"])
    font_brand = _load_font(FONT_BOLD_CANDIDATES, sizes["brand"])

    cal = _to_fa_digits(totals.get("calories", 0))
    protein = _to_fa_digits(totals.get("protein_g", 0))
    carbs = _to_fa_digits(totals.get("carbs_g", 0))
    fat = _to_fa_digits(totals.get("fat_g", 0))

    hero_text = f"{cal}"
    cal_label = "کالری"
    macro_text = f"💪 {protein}گ   🍞 {carbs}گ   🥑 {fat}گ"

    y_base = height - hero_h + max(16, hero_h // 10)
    fire_font = _load_font(FONT_REGULAR_CANDIDATES, sizes["macro"] + 4)
    draw.text((28, y_base), "🔥", font=fire_font, fill=(255, 200, 80, 255))
    draw.text((72, y_base - 8), hero_text, font=font_hero, fill=(255, 255, 255, 255))
    hw, _ = _text_size(draw, hero_text, font_hero)
    draw.text((82 + hw, y_base + 12), cal_label, font=font_macro, fill=(230, 230, 230, 255))

    draw.text((28, y_base + max(48, hero_h // 2.8)), macro_text, font=font_macro, fill=(240, 240, 240, 255))

    bw, bh = _text_size(draw, BRAND_TEXT, font_brand)
    brand_pad = 12
    bx1 = width - bw - brand_pad * 2 - 20
    by1 = 20
    bx2 = width - 20
    by2 = by1 + bh + brand_pad * 2
    _rounded_rect(draw, (bx1, by1, bx2, by2), 14, fill=(0, 0, 0, 130))
    draw.text((bx1 + brand_pad, by1 + brand_pad), BRAND_TEXT, font=font_brand, fill=(255, 255, 255, 240))

    return base.convert("RGB")


def create_tagged_image(
    image_bytes: bytes,
    items: list,
    totals: dict | None = None,
) -> tuple[bytes, list]:
    """استایل A: تگ عدد کالری + hero پایین. خروجی: (JPEG bytes, آیتم‌های بدون تگ)."""
    img = _prepare_image(image_bytes)
    width, height = img.size
    sizes = _scale_sizes(width)
    draw = ImageDraw.Draw(img)

    font_tag = _load_font(FONT_BOLD_CANDIDATES, sizes["tag"])
    hero_h = max(int(height * 0.26), int(width * 0.22))
    hero_h = min(hero_h, int(height * 0.36))

    tagged_items = []
    untagged = []
    placed_rects = []

    sortable = []
    for item in items:
        center = _box_center(item.get("box_2d"), width, height)
        if center is None:
            untagged.append(item)
            continue
        sortable.append((item, center))

    sortable.sort(key=lambda x: x[1][1])

    for item, center in sortable[:MAX_ON_IMAGE_TAGS]:
        cal_text = _to_fa_digits(item.get("calories", 0))
        tw, th = _text_size(draw, cal_text, font_tag)
        pad_x = sizes["pad_x"]
        pad_y = sizes["pad_y"]
        tag_w = tw + pad_x * 2
        tag_h = th + pad_y * 2

        pos = _place_tag(center, tag_w, tag_h, width, height, hero_h, placed_rects, len(placed_rects))
        if pos is None:
            untagged.append(item)
            continue

        tx, ty = pos
        tag_rect = (tx, ty, tx + tag_w, ty + tag_h)
        tag_cx = (tag_rect[0] + tag_rect[2]) // 2
        tag_cy = (tag_rect[1] + tag_rect[3]) // 2
        _draw_leader(draw, center, (tag_cx, tag_cy), sizes)
        placed_rects.append(_draw_pill_tag(draw, (tx, ty), cal_text, font_tag, sizes))
        tagged_items.append(item)

    for item, _ in sortable[MAX_ON_IMAGE_TAGS:]:
        untagged.append(item)

    if totals:
        img = _draw_hero_bar(img, totals)

    out = io.BytesIO()
    img.save(out, format="JPEG", quality=92)
    return out.getvalue(), untagged


def format_untagged_lines(items: list) -> str:
    if not items:
        return ""
    lines = ["📋 *جزئیات بقیه مواد:*"]
    for item in items:
        qty = f" ({item['quantity']})" if item.get("quantity") else ""
        lines.append(f"• {item.get('name', '')}{qty}: *{item.get('calories', 0)}* کالری")
    return "\n".join(lines)
