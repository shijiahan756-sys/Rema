from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field

from file_utils import get_image_size

CHROME_CANDIDATES = (
    os.environ.get("REMA_CHROME", ""),
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    "/usr/bin/google-chrome",
    "/usr/bin/chromium",
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
)


@dataclass
class ToolResult:
    ok: bool
    path: str = ""
    reason: str = ""
    meta: dict = field(default_factory=dict)


def find_chrome():
    for c in CHROME_CANDIDATES:
        if c and os.path.exists(c):
            return c
    for name in ("chrome", "google-chrome", "chromium", "msedge"):
        p = shutil.which(name)
        if p:
            return p
    return None


def _have_pil():
    try:
        import PIL
        return True
    except Exception:
        return False


def tools_available():
    return {"pillow": _have_pil(), "chrome": bool(find_chrome())}


def _norm_bbox(bbox, width=None, height=None):
    if bbox is None:
        raise ValueError("bbox required")
    if len(bbox) == 4 and bbox[2] > 0 and bbox[3] > 0:
        x, y, w, h = [float(v) for v in bbox]
    else:
        x1, y1, x2, y2 = [float(v) for v in bbox]
        x, y, w, h = x1, y1, x2 - x1, y2 - y1
    if width is not None:
        x = max(0.0, min(x, width - 1))
        w = min(w, width - x)
    if height is not None:
        y = max(0.0, min(y, height - 1))
        h = min(h, height - y)
    return int(round(x)), int(round(y)), max(1, int(round(w))), max(1, int(round(h)))


def crop_zoom(image_path, bbox, out_path, scale=2.0):
    if not image_path or not os.path.exists(image_path):
        return ToolResult(False, reason=f"image not found: {image_path}")
    try:
        width, height = get_image_size(image_path)
        x, y, w, h = _norm_bbox(bbox, width, height)
    except Exception as e:
        return ToolResult(False, reason=f"bad bbox: {e}")

    os.makedirs(os.path.dirname(os.path.abspath(out_path)) or ".", exist_ok=True)

    if _have_pil():
        try:
            from PIL import Image
            with Image.open(image_path) as im:
                crop = im.convert("RGB").crop((x, y, x + w, y + h))
                crop = crop.resize((max(1, int(w * scale)), max(1, int(h * scale))),
                                   Image.LANCZOS)
                crop.save(out_path)
            return ToolResult(True, out_path, meta={"backend": "pillow", "bbox": [x, y, w, h]})
        except Exception as e:
            pass

    chrome = find_chrome()
    if not chrome:
        return ToolResult(False, reason="no Pillow and no Chrome/Edge available")

    W, H = max(1, int(w * scale)), max(1, int(h * scale))
    html = (
        '<!DOCTYPE html><html><head><meta charset="utf-8"><style>'
        "html,body{margin:0;padding:0;overflow:hidden;background:#fff}"
        f"#c{{width:{W}px;height:{H}px;overflow:hidden;position:relative}}"
        f"#i{{position:absolute;left:{-x * scale}px;top:{-y * scale}px;"
        f"width:{width * scale}px}}"
        "</style></head><body><div id=\"c\"><img id=\"i\" src=\"file:///"
        + os.path.abspath(image_path).replace("\\", "/")
        + '"></div></body></html>'
    )
    return _screenshot_html(chrome, html, W, H, out_path, meta={"bbox": [x, y, w, h]})


def annotate_region(image_path, bbox, marks, out_path, scale=2.0):
    if not image_path or not os.path.exists(image_path):
        return ToolResult(False, reason=f"image not found: {image_path}")
    try:
        width, height = get_image_size(image_path)
        x, y, w, h = _norm_bbox(bbox, width, height)
    except Exception as e:
        return ToolResult(False, reason=f"bad bbox: {e}")
    marks = [m for m in (marks or []) if m.get("box")]
    os.makedirs(os.path.dirname(os.path.abspath(out_path)) or ".", exist_ok=True)

    if _have_pil():
        try:
            from PIL import Image, ImageDraw, ImageFont
            with Image.open(image_path) as im:
                crop = im.convert("RGB").crop((x, y, x + w, y + h))
                crop = crop.resize((max(1, int(w * scale)), max(1, int(h * scale))),
                                   Image.LANCZOS)
                dr = ImageDraw.Draw(crop, "RGBA")
                try:
                    font = ImageFont.load_default()
                except Exception:
                    font = None
                for m in marks:
                    bx, by, bw, bh = _norm_bbox(m["box"], width, height)
                    rx = (bx - x) * scale
                    ry = (by - y) * scale
                    rw, rh = bw * scale, bh * scale
                    dr.rectangle([rx, ry, rx + rw, ry + rh], outline=m.get("color", "#D85A30"),
                                 width=3)
                    if m.get("label"):
                        tx, ty = rx + 3, max(0, ry - 14)
                        tw = max(8 * len(str(m["label"])), 40)
                        dr.rectangle([tx, ty, tx + tw, ty + 14], fill=(255, 255, 255, 220))
                        dr.text((tx + 2, ty + 1), str(m["label"]), fill=(30, 30, 30), font=font)
                crop.save(out_path)
            return ToolResult(True, out_path, meta={"backend": "pillow", "marks": len(marks)})
        except Exception:
            pass

    chrome = find_chrome()
    if not chrome:
        return ToolResult(False, reason="no Pillow and no Chrome/Edge available")
    W, H = max(1, int(w * scale)), max(1, int(h * scale))
    boxes_html = []
    for m in marks:
        bx, by, bw, bh = _norm_bbox(m["box"], width, height)
        left, top = (bx - x) * scale, (by - y) * scale
        color = m.get("color", "#D85A30")
        boxes_html.append(
            f'<div class="mk" style="left:{left}px;top:{top}px;width:{bw * scale}px;'
            f'height:{bh * scale}px;border-color:{color}"></div>'
            + (f'<div class="lb" style="left:{left}px;top:{max(0, top - 15)}px;'
               f'border-color:{color}">{m["label"]}</div>' if m.get("label") else "")
        )
    html = (
        '<!DOCTYPE html><html><head><meta charset="utf-8"><style>'
        "html,body{margin:0;padding:0;overflow:hidden;background:#fff}"
        f"#c{{width:{W}px;height:{H}px;overflow:hidden;position:relative}}"
        f"#i{{position:absolute;left:{-x * scale}px;top:{-y * scale}px;width:{width * scale}px}}"
        ".mk{position:absolute;border:3px solid #D85A30;box-sizing:border-box}"
        ".lb{position:absolute;font:11px/15px sans-serif;background:#fff;padding:0 3px;"
        "border:1px solid #D85A30;color:#222;z-index:9}"
        "</style></head><body><div id=\"c\"><img id=\"i\" src=\"file:///"
        + os.path.abspath(image_path).replace("\\", "/")
        + '">' + "".join(boxes_html) + "</div></body></html>"
    )
    return _screenshot_html(chrome, html, W, H, out_path, meta={"marks": len(marks)})


def _screenshot_html(chrome, html, W, H, out_path, meta=None):
    tmp_html = None
    tmp_png = os.path.abspath(out_path)
    try:
        fd, tmp_html = tempfile.mkstemp(suffix=".html")
        os.close(fd)
        with open(tmp_html, "w", encoding="utf-8") as f:
            f.write(html)
        cmd = [chrome, "--headless=new", "--disable-gpu", "--hide-scrollbars",
               "--allow-file-access-from-files", f"--window-size={W},{H}",
               f"--screenshot={tmp_png}", "file:///" + tmp_html.replace("\\", "/")]
        subprocess.run(cmd, capture_output=True, timeout=90, check=False)
        if os.path.exists(tmp_png) and os.path.getsize(tmp_png) > 0:
            return ToolResult(True, tmp_png, meta=dict(meta or {}, backend="chrome"))
        return ToolResult(False, reason="chrome screenshot produced no file")
    except Exception as e:
        return ToolResult(False, reason=f"chrome screenshot failed: {e}")
    finally:
        if tmp_html and os.path.exists(tmp_html):
            try:
                os.remove(tmp_html)
            except Exception:
                pass


def region_ocr(image_path, bbox=None, lang="eng+chi_sim"):
    try:
        import pytesseract
        from PIL import Image
    except Exception:
        return None, "OCR unavailable (pytesseract/Pillow not installed)"
    try:
        with Image.open(image_path) as im:
            img = im
            if bbox:
                w, h = im.size
                x, y, bw, bh = _norm_bbox(bbox, w, h)
                img = im.crop((x, y, x + bw, y + bh))
            txt = pytesseract.image_to_string(img, lang=lang)
        return txt.strip(), ""
    except Exception as e:
        return None, f"OCR failed: {e}"


def self_test():
    env = tools_available()
    print("tools:", env)
    img = os.path.join("image", "com024.jpg")
    if not os.path.exists(img):
        print("skip: sample image missing")
        return
    out_dir = os.path.join("Gemini", "crops")
    r = crop_zoom(img, (300, 450, 400, 300), os.path.join(out_dir, "selftest_crop.png"), 1.5)
    print("crop:", r.ok, r.reason or r.path)
    if r.ok:
        os.remove(r.path)
    r2 = annotate_region(img, (300, 450, 400, 300),
                         [{"box": (320, 500, 120, 80), "label": "Object15"}],
                         os.path.join(out_dir, "selftest_ann.png"), 1.5)
    print("annotate:", r2.ok, r2.reason or r2.path)
    if r2.ok:
        os.remove(r2.path)


if __name__ == "__main__":
    self_test()
