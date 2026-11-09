from __future__ import annotations

import base64
import os
import re
import xml.etree.ElementTree as ET

BPMN = "{http://www.omg.org/spec/BPMN/20100524/MODEL}"
DI = "{http://www.omg.org/spec/BPMN/20100524/BPMNDiagram}"
BPMNDI = "{http://www.omg.org/spec/BPMN/20100524/DI}"
DDDI = "{http://www.omg.org/spec/DD/20100524/DI}"
DC = "{http://www.omg.org/spec/DD/20100524/DC}"

INK = "#2C2C2A"
STROKE = 1.4
FONT = 'font-family="Arial, sans-serif" font-size="10" fill="%s"' % INK


def _local(tag):
    return tag.rsplit("}", 1)[-1] if isinstance(tag, str) else ""


def _num(v, default=0.0):
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def _esc(s):
    return (s or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;") \
        .replace('"', "&quot;")


def _label(text, cx, cy, max_w=110):
    if not text:
        return ""
    text = str(text).strip()
    if len(text) > 22:
        text = text[:21] + "…"
    return f'<text {FONT} text-anchor="middle" x="{cx:.0f}" y="{cy:.0f}">{_esc(text)}</text>'


def _wrap_label(text, cx, cy, width=100, line_h=12):
    if not text:
        return ""
    words = str(text).split()
    lines, cur = [], ""
    for w in words:
        if len(cur) + len(w) + 1 > 16 and cur:
            lines.append(cur)
            cur = w
        else:
            cur = (cur + " " + w).strip()
    if cur:
        lines.append(cur)
    lines = lines[:3]
    out = []
    for i, ln in enumerate(lines):
        out.append(f'<text {FONT} text-anchor="middle" x="{cx:.0f}" '
                   f'y="{cy + i * line_h:.0f}">{_esc(ln)}</text>')
    return "".join(out)


def collect_di(root):
    def _first(tag):
        return next(root.iter(tag), None) is not None

    shapes, edges = [], []
    ns_ok = _first(BPMNDI + "BPMNShape") or _first(BPMNDI + "BPMNEdge")
    shape_tag = BPMNDI + "BPMNShape" if ns_ok else DI + "BPMNShape"
    edge_tag = BPMNDI + "BPMNEdge" if ns_ok else DI + "BPMNEdge"
    for s in root.iter(shape_tag):
        b = None
        for cand in (DC + "Bounds", DDDI + "Bounds", BPMNDI + "Bounds"):
            b = s.find(cand)
            if b is not None:
                break
        if b is None:
            continue
        shapes.append({
            "id": s.get("id"), "element": s.get("bpmnElement"),
            "x": _num(b.get("x")), "y": _num(b.get("y")),
            "w": _num(b.get("width"), 30), "h": _num(b.get("height"), 30),
            "is_lane": s.get("isHorizontal") in ("true", "1"),
        })
    for s in root.iter(edge_tag):
        pts = []
        for wp in s.iter(DDDI + "waypoint"):
            pts.append((_num(wp.get("x")), _num(wp.get("y"))))
        if len(pts) >= 2:
            edges.append({"id": s.get("id"), "element": s.get("bpmnElement"), "points": pts})
    return shapes, edges, ns_ok


def _semantic_kinds(root):
    kinds = {}
    for el in root.iter():
        if not isinstance(el.tag, str) or not el.tag.startswith(BPMN):
            continue
        t = _local(el.tag)
        eid = el.get("id")
        if not eid:
            continue
        kinds[eid] = t
    return kinds


def _shape_svg(kind, x, y, w, h, name, semantic_kind, origin_is_collab=False):
    cx, cy = x + w / 2.0, y + h / 2.0
    parts = []
    if kind in ("task", "userTask", "serviceTask", "manualTask", "scriptTask",
                "sendTask", "receiveTask", "businessRuleTask", "callActivity"):
        parts.append(f'<rect x="{x:.0f}" y="{y:.0f}" width="{w:.0f}" height="{h:.0f}" rx="6" '
                     f'fill="#FFFFFF" stroke="{INK}" stroke-width="{STROKE}"/>')
        parts.append(_wrap_label(name, cx, cy - 4, w))
    elif kind == "subProcess":
        parts.append(f'<rect x="{x:.0f}" y="{y:.0f}" width="{w:.0f}" height="{h:.0f}" rx="10" '
                     f'fill="#FFFFFF" stroke="{INK}" stroke-width="{STROKE}"/>')
        parts.append(_wrap_label(name, cx, y + 14, w))
    elif kind in ("startEvent", "endEvent", "intermediateCatchEvent",
                  "intermediateThrowEvent", "boundaryEvent"):
        r = max(w, h) / 2.0
        sw = 3.0 if kind == "endEvent" else 1.4
        if kind in ("boundaryEvent", "intermediateCatchEvent", "intermediateThrowEvent"):
            parts.append(f'<circle cx="{cx:.0f}" cy="{cy:.0f}" r="{r:.0f}" fill="#FFFFFF" '
                         f'stroke="{INK}" stroke-width="{STROKE}"/>')
            r = r - 4
        parts.append(f'<circle cx="{cx:.0f}" cy="{cy:.0f}" r="{r:.0f}" fill="#FFFFFF" '
                     f'stroke="{INK}" stroke-width="{sw}"/>')
        parts.append(_label(name, cx, y + h + 11))
    elif kind in ("exclusiveGateway", "parallelGateway", "inclusiveGateway",
                  "eventBasedGateway", "complexGateway", "gateway"):
        parts.append(f'<path d="M{cx:.0f} {y:.0f} L{x + w:.0f} {cy:.0f} L{cx:.0f} {y + h:.0f} '
                     f'L{x:.0f} {cy:.0f} Z" fill="#FFFFFF" stroke="{INK}" stroke-width="{STROKE}"/>')
        marker = {"exclusiveGateway": "M%s" % "", "parallelGateway": "+"}.get(kind, "X")
        if kind == "exclusiveGateway":
            m = w * 0.22
            parts.append(f'<path d="M{cx - m:.0f} {cy - m:.0f} L{cx + m:.0f} {cy + m:.0f} '
                         f'M{cx - m:.0f} {cy + m:.0f} L{cx + m:.0f} {cy - m:.0f}" '
                         f'stroke="{INK}" stroke-width="{STROKE}" fill="none"/>')
        elif kind == "parallelGateway":
            m = w * 0.26
            parts.append(f'<path d="M{cx:.0f} {cy - m:.0f} L{cx:.0f} {cy + m:.0f} '
                         f'M{cx - m:.0f} {cy:.0f} L{cx + m:.0f} {cy:.0f}" '
                         f'stroke="{INK}" stroke-width="{STROKE}" fill="none"/>')
        elif kind == "inclusiveGateway":
            r = w * 0.26
            parts.append(f'<circle cx="{cx:.0f}" cy="{cy:.0f}" r="{r:.0f}" fill="none" '
                         f'stroke="{INK}" stroke-width="{STROKE}"/>')
    elif kind == "dataObjectReference":
        fold = max(8.0, w * 0.22)
        parts.append(f'<path d="M{x:.0f} {y:.0f} L{x + w - fold:.0f} {y:.0f} L{x + w:.0f} '
                     f'{y + fold:.0f} L{x + w:.0f} {y + h:.0f} L{x:.0f} {y + h:.0f} Z" '
                     f'fill="#FFFFFF" stroke="{INK}" stroke-width="{STROKE}"/>')
        parts.append(f'<path d="M{x + w - fold:.0f} {y:.0f} L{x + w - fold:.0f} {y + fold:.0f} '
                     f'L{x + w:.0f} {y + fold:.0f}" fill="none" stroke="{INK}" '
                     f'stroke-width="{STROKE}"/>')
        parts.append(_label(name, cx, y + h + 11))
    elif kind == "dataObject":
        parts.append(f'<rect x="{x:.0f}" y="{y:.0f}" width="{w:.0f}" height="{h:.0f}" rx="2" '
                     f'fill="#FFFFFF" stroke="{INK}" stroke-width="{STROKE}" '
                     f'stroke-dasharray="4 3" opacity="0.75"/>')
        parts.append(_label(name, cx, cy - 4))
    elif kind == "dataStoreReference":
        ry = min(10.0, h * 0.25)
        parts.append(f'<path d="M{x:.0f} {y + ry:.0f} L{x:.0f} {y + h - ry:.0f} '
                     f'A{w / 2:.0f} {ry:.0f} 0 0 0 {x + w:.0f} {y + h - ry:.0f} '
                     f'L{x + w:.0f} {y + ry:.0f}" fill="#FFFFFF" stroke="{INK}" '
                     f'stroke-width="{STROKE}"/>')
        parts.append(f'<ellipse cx="{cx:.0f}" cy="{y + ry:.0f}" rx="{w / 2:.0f}" ry="{ry:.0f}" '
                     f'fill="#FFFFFF" stroke="{INK}" stroke-width="{STROKE}"/>')
        parts.append(_label(name, cx, y + h + 12))
    elif kind == "textAnnotation":
        parts.append(f'<rect x="{x:.0f}" y="{y:.0f}" width="{w:.0f}" height="{h:.0f}" rx="4" '
                     f'fill="#FFFFFF" stroke="{INK}" stroke-width="1" '
                     f'stroke-dasharray="5 3"/>')
        parts.append(_wrap_label(name, cx, cy - 4, w))
    elif kind == "participant":
        parts.append(f'<rect x="{x:.0f}" y="{y:.0f}" width="{w:.0f}" height="{h:.0f}" '
                     f'fill="none" stroke="{INK}" stroke-width="2.4"/>')
        parts.append(_wrap_label(name, x + 16, cy - 4, 20))
    elif kind == "lane":
        parts.append(f'<rect x="{x:.0f}" y="{y:.0f}" width="{w:.0f}" height="{h:.0f}" '
                     f'fill="none" stroke="{INK}" stroke-width="1.2"/>')
        parts.append(_wrap_label(name, x + 12, cy - 4, 16))
    else:
        parts.append(f'<rect x="{x:.0f}" y="{y:.0f}" width="{w:.0f}" height="{h:.0f}" rx="4" '
                     f'fill="#FFFFFF" stroke="{INK}" stroke-width="1" '
                     f'stroke-dasharray="3 3" opacity="0.7"/>')
        parts.append(_label(name or kind, cx, cy - 4))
    return "".join(parts)


def _edge_svg(kind, pts):
    d = "M" + " L".join(f"{x:.0f} {y:.0f}" for x, y in pts)
    dash = ""
    marker = "url(#arrSeq)"
    if kind == "messageFlow":
        dash = ' stroke-dasharray="7 4"'
        marker = "url(#arrMsg)"
    elif kind == "association":
        dash = ' stroke-dasharray="2 3"'
        marker = "url(#arrAssoc)"
    return (f'<path d="{d}" fill="none" stroke="{INK}" stroke-width="{STROKE}"{dash} '
            f'marker-end="{marker}"/>')


def render_bpmn_to_svg(bpmn_xml):
    root = ET.fromstring(bpmn_xml)
    shapes, edges, di_ok = collect_di(root)
    kinds = _semantic_kinds(root)
    names = {}
    for el in root.iter():
        if isinstance(el.tag, str) and el.get("id") and el.get("name"):
            names[el.get("id")] = el.get("name")

    if shapes:
        x1 = min(s["x"] for s in shapes)
        y1 = min(s["y"] for s in shapes)
        x2 = max(s["x"] + s["w"] for s in shapes)
        y2 = max(s["y"] + s["h"] for s in shapes)
    else:
        x1, y1, x2, y2 = 0, 0, 600, 400
    for e in edges:
        for px, py in e["points"]:
            x1, y1 = min(x1, px), min(y1, py)
            x2, y2 = max(x2, px), max(y2, py)
    pad = 30
    ox, oy = x1 - pad, y1 - pad
    W, H = (x2 - x1) + 2 * pad, (y2 - y1) + 2 * pad

    order = {"participant": 0, "lane": 1}
    shapes_sorted = sorted(
        shapes,
        key=lambda s: order.get(kinds.get(s["element"], ""), 2))

    body = []
    for s in shapes_sorted:
        kind = kinds.get(s["element"], "unknown")
        body.append(_shape_svg(kind, s["x"] - ox, s["y"] - oy, s["w"], s["h"],
                               names.get(s["element"], ""), kind))
    for e in edges:
        kind = kinds.get(e["element"], "sequenceFlow")
        pts = [(px - ox, py - oy) for px, py in e["points"]]
        body.append(_edge_svg(kind, pts))

    svg = (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{W:.0f}" height="{H:.0f}" '
        f'viewBox="0 0 {W:.0f} {H:.0f}">'
        f'<defs>'
        f'<marker id="arrSeq" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" '
        f'markerHeight="7" orient="auto-start-reverse">'
        f'<path d="M1 1 L9 5 L1 9 Z" fill="{INK}"/></marker>'
        f'<marker id="arrMsg" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="8" '
        f'markerHeight="8" orient="auto-start-reverse">'
        f'<path d="M1 1 L9 5 L1 9" fill="none" stroke="{INK}" stroke-width="1.3"/></marker>'
        f'<marker id="arrAssoc" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="8" '
        f'markerHeight="8" orient="auto-start-reverse">'
        f'<path d="M1 1 L9 5 L1 9" fill="none" stroke="{INK}" stroke-width="1.3"/></marker>'
        f'</defs>'
        f'<rect x="0" y="0" width="{W:.0f}" height="{H:.0f}" fill="#FFFFFF"/>'
        + "".join(body) + "</svg>"
    )
    info = {
        "shapes": len(shapes), "edges": len(edges),
        "di_namespace_standard": di_ok,
        "semantic_elements": len(kinds),
        "canvas": [round(W), round(H)],
    }
    return svg, info


def svg_to_png(svg_text, out_png, chrome_path=None, tmp_svg_path=None):
    from image_tools import ToolResult, _screenshot_html, find_chrome
    if chrome_path is None:
        chrome_path = find_chrome()
    if not chrome_path:
        return ToolResult(False, reason="no Chrome/Edge available for rasterization")
    m = re.search(r'width="(\d+(?:\.\d+)?)" height="(\d+(?:\.\d+)?)"', svg_text)
    W = min(4000, max(200, int(float(m.group(1))) if m else 800) + 10)
    H = min(4000, max(200, int(float(m.group(2))) if m else 600) + 10)
    svg_path = os.path.abspath(tmp_svg_path or (os.path.splitext(os.path.abspath(out_png))[0] + "_src.svg"))
    os.makedirs(os.path.dirname(svg_path), exist_ok=True)
    with open(svg_path, "w", encoding="utf-8") as f:
        f.write(svg_text)
    html = (
        "<!DOCTYPE html><html><head><meta charset='utf-8'><style>"
        "html,body{margin:0;padding:0;overflow:hidden;background:#fff}</style></head>"
        "<body><img src='file:///" + svg_path.replace("\\", "/") + "'></body></html>"
    )
    r = _screenshot_html(chrome_path, html, W, H, out_png, meta={"svg_path": svg_path})
    try:
        if os.path.exists(svg_path):
            os.remove(svg_path)
    except Exception:
        pass
    return r


def render_bpmn(bpmn_xml, out_svg, out_png=None):
    svg, info = render_bpmn_to_svg(bpmn_xml)
    os.makedirs(os.path.dirname(os.path.abspath(out_svg)) or ".", exist_ok=True)
    with open(out_svg, "w", encoding="utf-8") as f:
        f.write(svg)
    info["svg_path"] = out_svg
    if out_png:
        r = svg_to_png(svg, out_png)
        info["png_ok"] = r.ok
        info["png_reason"] = r.reason
        if r.ok:
            info["png_path"] = out_png
    return info


def self_test():
    sample = os.path.join("Gemini_all", "com024_processed.bpmn")
    out_dir = os.path.join("Gemini", "render")
    svg, info = render_bpmn_to_svg(open(sample, encoding="utf-8").read())
    print("svg info:", info)
    assert info["shapes"] > 0 and info["edges"] > 0
    assert info["di_namespace_standard"]
    r = svg_to_png(svg, os.path.join(out_dir, "selftest_com024.png"))
    print("png:", r.ok, r.reason)
    if r.ok:
        print("png size:", os.path.getsize(r.path))
    print("renderer self-test OK")


if __name__ == "__main__":
    self_test()
