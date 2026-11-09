from __future__ import annotations

import json
import os
import re

import renderer
from config import OUTPUT_SUBDIR, RENDER_SUBDIR
from file_utils import save_json

COMPARE_SYSTEM = """You compare a hand-drawn BPMN sketch (Image A) with a rendering of its BPMN
reconstruction (Image B). List ONLY differences visible when comparing them:
  - shapes/lines present in A but missing in B;
  - shapes/lines present in B but not in A.
Do NOT judge layout aesthetics, arrow routing, or handwriting quality.
Elements that are marked as auto-completed (origin=normalized) must be IGNORED.
Answer ONLY with JSON:
{"missing":[{"class":"...","label":"...","where":"..."}],
 "extra":[{"class":"...","label":"...","where":"..."}]}"""


def render_xml(xml_text, out_base, chrome_path=None):
    os.makedirs(os.path.dirname(os.path.abspath(out_base)) or ".", exist_ok=True)
    info = renderer.render_bpmn(xml_text, out_base + ".svg",
                                out_png=(out_base + ".png") if chrome_path is not False else None)
    return info


def _parse_json_loose(raw):
    txt = re.sub(r"^```(?:json)?|```$", "", (raw or "").strip(), flags=re.MULTILINE).strip()
    start, end = txt.find("{"), txt.rfind("}")
    if start >= 0 and end > start:
        txt = txt[start:end + 1]
    return json.loads(txt)


def diff_with_model(original_image_path, rendered_png, elements_text, call_model_func):
    from file_utils import detect_image_mime, image_to_base64
    if not rendered_png or not os.path.exists(rendered_png):
        return None
    items = []
    try:
        envelope = json.loads(elements_text) if isinstance(elements_text, str) else elements_text
        items = [(eid, b) for eid, b in __import__("bpmn_geometry").iter_elements(envelope)
                 if b.get("origin") not in ("normalized",)]
        elements_compact = json.dumps(
            {"elements": [{eid: b} for eid, b in items]}, ensure_ascii=False)[:6000]
    except Exception:
        elements_compact = elements_text[:6000] if elements_text else "[]"
    text = (
        "Image A = original hand-drawn sketch. Image B = BPMN rendering of the reconstruction.\n"
        "Element inventory of B (for reference; origin=normalized items are auto-completed, ignore "
        f"their absence):\n{elements_compact}\n\n"
        "Report differences as specified. JSON only."
    )
    try:
        ma = detect_image_mime(original_image_path)
        mb = detect_image_mime(rendered_png)
        content = [
            {"type": "text", "text": text},
            {"type": "image_url",
             "image_url": {"url": f"data:{ma};base64,{image_to_base64(original_image_path)}"}},
            {"type": "image_url",
             "image_url": {"url": f"data:{mb};base64,{image_to_base64(rendered_png)}"}},
        ]
        raw, _ = call_model_func([{"role": "system", "content": COMPARE_SYSTEM},
                                  {"role": "user", "content": content}])
        parsed = _parse_json_loose(raw)
    except Exception as e:
        print(f"    [render-compare] 比对调用失败: {e}")
        return None
    return {
        "missing": [e for e in (parsed.get("missing") or []) if isinstance(e, dict)],
        "extra": [e for e in (parsed.get("extra") or []) if isinstance(e, dict)],
    }


def _diff_to_findings(diff):
    findings = []
    for kind in ("missing", "extra"):
        for e in diff.get(kind) or []:
            findings.append({
                "rule_id": "RC3",
                "element_ids": [],
                "field": kind,
                "message": (f"Render-back ({kind}): {e.get('class', '?')} "
                            f"'{e.get('label', '?')}' {e.get('where', '')}"),
            })
    return findings


def compare_with_model(original_image_path, rendered_png, elements_text, call_model_func):
    diff = diff_with_model(original_image_path, rendered_png, elements_text, call_model_func)
    if diff is None:
        return None
    return _diff_to_findings(diff)


def run_render_compare(validated_xml, elements_text, image_path, call_model_func,
                       ablation=None, output_dir=None, base_name=None,
                       rules_file=None):
    from image_tools import find_chrome
    ablation = ablation or {}
    info = {
        "mode": "report_only",
        "rendered": False,
        "diff_count": 0,
        "missing": [],
        "extra": [],
        "findings": [],
        "rounds": [],
        "skipped": None,
        "note": "Report only — no JSON feedback, no regeneration, no multi-round loop.",
    }
    if not ablation.get("render_compare", True):
        info["skipped"] = "ablation render_compare=false"
        return validated_xml, elements_text, info

    chrome = find_chrome()
    render_dir = os.path.join(output_dir, RENDER_SUBDIR) if output_dir else None
    out_base = os.path.join(render_dir, f"{base_name}_render") if render_dir else None
    if out_base is None:
        import tempfile
        out_base = os.path.join(tempfile.gettempdir(), f"{base_name}_render")

    try:
        rinfo = render_xml(validated_xml, out_base, chrome_path=chrome)
    except Exception as e:
        info["skipped"] = f"render failed: {e}"
        print(f"    [render-compare] 渲染失败，跳过比对（不阻断主管线）: {e}")
        return validated_xml, elements_text, info

    info["rendered"] = True
    round_rec = {"round": 1, "render": {k: v for k, v in rinfo.items() if k != "svg_path"}}
    info["rounds"].append(round_rec)

    png = rinfo.get("png_path")
    if not rinfo.get("png_ok"):
        info["skipped"] = f"png unavailable: {rinfo.get('png_reason')}"
        print(f"    [render-compare] PNG 不可用（{rinfo.get('png_reason')}），只保留 SVG 渲染记录")
    elif call_model_func is None:
        info["skipped"] = "no model for visual diff"
    else:
        diff = diff_with_model(image_path, png, elements_text, call_model_func)
        if diff is None:
            info["skipped"] = "compare failed"
        else:
            info["missing"] = diff["missing"]
            info["extra"] = diff["extra"]
            info["findings"] = _diff_to_findings(diff)
            info["diff_count"] = len(diff["missing"]) + len(diff["extra"])
            round_rec["diff_count"] = info["diff_count"]
            print(f"    [render-compare] 报告：缺失 {len(diff['missing'])} 处，"
                  f"多出 {len(diff['extra'])} 处（仅记录，不回灌）")

    if output_dir and base_name:
        save_json(output_dir, OUTPUT_SUBDIR,
                  f"{base_name}_step4_render_compare.json", info)
    return validated_xml, elements_text, info


def run_render_compare_loop(validated_xml, elements_text, image_path, call_model_func,
                            ablation=None, output_dir=None, base_name=None,
                            rules_file=None, regenerate=None):
    if regenerate is not None:
        print("    [render-compare] 已切换为报告制：忽略回灌回调，不做重新生成")
    return run_render_compare(validated_xml, elements_text, image_path, call_model_func,
                              ablation=ablation, output_dir=output_dir,
                              base_name=base_name, rules_file=rules_file)


def self_test():
    xml = open("Gemini_all/com024_processed.bpmn", encoding="utf-8").read()
    out_base = os.path.join("Gemini", "render", "selftest_rc")
    info = render_xml(xml, out_base, chrome_path=False)
    assert info["shapes"] > 0 and info["edges"] > 0
    assert os.path.exists(out_base + ".svg")
    os.remove(out_base + ".svg")

    els = json.dumps({"elements": []})
    out2, els2, info2 = run_render_compare(
        xml, els, "image/com024.jpg", None,
        ablation={"render_compare": True}, output_dir=None, base_name="selftest")
    assert info2.get("skipped")
    assert info2["mode"] == "report_only"
    assert out2 == xml and els2 == els, "report-only 不得修改输入"

    _x, _e, info3 = run_render_compare_loop(
        xml, els, "image/com024.jpg", None,
        ablation={"render_compare": True}, output_dir=None, base_name="selftest")
    assert info3.get("skipped") and info3["mode"] == "report_only"
    print("render_compare self-test OK")


if __name__ == "__main__":
    self_test()
