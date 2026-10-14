import os
from PIL import Image, ImageDraw, ImageFont

# MT5-ish palette
COLOURS = {
    "entry":  (30, 144, 255),    # dodger blue
    "stop":   (220, 40, 40),     # red
    "target": (40, 180, 80),     # green
    "level":  (255, 180, 0),     # amber
    "note":   (170, 90, 220),    # purple
    "zone":   (255, 180, 0),     # amber (shaded band)
}

# Line style per type: (width, dash_on, dash_off)  dash_off=0 means solid
STYLE = {
    "entry":  (2, 0, 0),      # solid — the trigger
    "stop":   (2, 8, 6),      # dashed
    "target": (2, 8, 6),      # dashed
    "level":  (1, 4, 6),      # fine dotted
    "note":   (1, 2, 6),      # very fine
}


def _font(size):
    for name in ("segoeui.ttf", "arial.ttf", "tahoma.ttf", "DejaVuSans.ttf"):
        try:
            return ImageFont.truetype(name, size)
        except Exception:
            continue
    return ImageFont.load_default()


def _dashed_line(draw, y, w, colour, width, on, off):
    if off <= 0:
        draw.line([(0, y), (w, y)], fill=colour, width=width)
        return
    x = 0
    while x < w:
        draw.line([(x, y), (min(x + on, w), y)], fill=colour, width=width)
        x += on + off


def markup_chart(image_path: str, annotations: str, output_name: str = "",
                 legend: str = "") -> str:
    """Draw MT5-style analysis onto a chart image and save/open an annotated copy.

    annotations: semicolon-separated items, each "type,position,label"
      type     = entry | stop | target | level | note | zone
      position = percent from TOP of image (0-100). For 'zone' use "top-bottom"
                 e.g. "zone,55-62,Entry zone"
      label    = short text (kept small, pinned to the right edge)

    Example:
      "level,12,R 1.1100; zone,55-62,Entry zone; entry,58,BUY 1.0920;
       stop,74,SL 1.0800; target,30,TP1 1.1050"

    legend: optional short line of context drawn small in the top-left corner.
    """
    try:
        if not os.path.exists(image_path):
            return f"Image not found: {image_path}"

        img = Image.open(image_path).convert("RGB")
        w, h = img.size

        # Overlay layer so zones can be semi-transparent
        overlay = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        od = ImageDraw.Draw(overlay)
        draw = ImageDraw.Draw(img)

        # Small but legible: ~2% of height, clamped
        fsize = max(10, min(16, int(h * 0.022)))
        font = _font(fsize)

        drawn = []
        placed = []

        # --- pass 1: zones (shaded bands, drawn under the lines) ---
        for raw in annotations.split(";"):
            parts = [p.strip() for p in raw.split(",")]
            if len(parts) < 3 or parts[0].lower() != "zone":
                continue
            span = parts[1].replace("–", "-")
            try:
                a, b = [float(v) for v in span.split("-")]
            except ValueError:
                continue
            y1, y2 = sorted((int(h * a / 100), int(h * b / 100)))
            od.rectangle([(0, y1), (w, y2)], fill=COLOURS["zone"] + (48,))
            drawn.append(f"zone {a}-{b}% — {','.join(parts[2:]).strip()}")

        img = Image.alpha_composite(img.convert("RGBA"), overlay).convert("RGB")
        draw = ImageDraw.Draw(img)

        # --- pass 2: lines + right-edge price tags (MT5 style) ---
        for raw in annotations.split(";"):
            parts = [p.strip() for p in raw.split(",")]
            if len(parts) < 3:
                continue
            kind = parts[0].lower()
            if kind == "zone":
                # label the zone at its top edge
                span = parts[1].replace("–", "-")
                try:
                    a, _b = [float(v) for v in span.split("-")]
                except ValueError:
                    continue
                y = int(h * a / 100)
                label = ",".join(parts[2:]).strip()
                colour = COLOURS["zone"]
            else:
                try:
                    pct = float(parts[1])
                except ValueError:
                    continue
                y = max(1, min(h - 1, int(h * pct / 100)))
                label = ",".join(parts[2:]).strip()
                colour = COLOURS.get(kind, COLOURS["note"])
                width, on, off = STYLE.get(kind, (1, 4, 6))
                _dashed_line(draw, y, w, colour, width, on, off)

            # right-edge tag, like MT5's price label
            box = draw.textbbox((0, 0), label, font=font)
            tw, th = box[2] - box[0], box[3] - box[1]
            pad = 4
            x2 = w - 2
            x1 = x2 - (tw + pad * 2)
            tag_h = th + pad * 2
            ty = y - tag_h // 2
            # keep fully inside the image
            ty = max(2, min(h - tag_h - 2, ty))
            # nudge away from any label already placed
            for used_top, used_bot in placed:
                if ty < used_bot and ty + tag_h > used_top:
                    ty = used_bot + 3
            ty = max(2, min(h - tag_h - 2, ty))
            placed.append((ty, ty + tag_h))
            draw.rectangle([(x1, ty), (x2, ty + th + pad * 2)], fill=colour)
            draw.text((x1 + pad, ty + pad), label, fill=(255, 255, 255), font=font)

            if kind != "zone":
                drawn.append(f"{kind} @ {parts[1]}% — {label}")

        # --- optional small legend, top-left ---
        if legend:
            lfont = _font(max(9, fsize - 2))
            box = draw.textbbox((0, 0), legend, font=lfont)
            tw, th = box[2] - box[0], box[3] - box[1]
            draw.rectangle([(4, 4), (4 + tw + 10, 4 + th + 8)], fill=(20, 20, 20))
            draw.text((9, 8), legend, fill=(235, 235, 235), font=lfont)

        if not drawn:
            return ("No valid annotations parsed. Format: "
                    "'type,position,label; ...' where type is "
                    "entry/stop/target/level/note/zone and position is 0-100 "
                    "(zone uses 'top-bottom', e.g. 'zone,55-62,Entry zone').")

        base = os.path.splitext(os.path.basename(image_path))[0]
        out_path = os.path.join(os.getcwd(), output_name or f"{base}_marked.png")
        img.save(out_path)

        try:
            os.startfile(out_path)
        except Exception:
            pass

        return ("Marked-up chart saved and opened: " + out_path
                + "\nDrawn:\n  " + "\n  ".join(drawn))

    except Exception as e:
        return f"Error marking up chart: {e}"