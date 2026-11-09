from __future__ import annotations

import json
import os
import re

import bpmn_geometry as geo
import topology_checks as tc
from config import CROP_SUBDIR, OUTPUT_SUBDIR
from file_utils import save_json

LINE_CLASSIFY_SYSTEM = """You classify a drawn line in a cropped, magnified region of a hand-drawn
BPMN diagram. The shapes are marked with labeled boxes. Answer ONLY with JSON, no prose:
{"line_present": true|false, "line_style": "solid"|"dotted"|"dashed"|"none",
 "from_label": "<label or null>", "to_label": "<label or null>", "confidence": 0.0-1.0}
Judge ONLY what is visible in the crop."""


def class_consistency_checks(envelope):
    items = dict(geo.iter_elements(envelope))
    findings = []
    for eid, body in geo.iter_elements(envelope):
        cls = body.get("class")
        s, t = body.get("sourceRef"), body.get("targetRef")
        sb, tb = items.get(s), items.get(t)
        if cls == "SequenceFlow":
            bad = [ref for ref, b in ((s, sb), (t, tb))
                   if b is not None and b.get("class") in geo.ARTIFACTS]
            if bad:
                findings.append({
                    "rule_id": "R4", "element_ids": [eid], "field": "class",
                    "message": (f"SequenceFlow {eid} has artifact endpoint {bad}; a sequence "
                                f"flow cannot connect a data object. This line is most likely a "
                                f"dotted Association misread as a solid line — re-check the line "
                                f"style and change the class if confirmed."),
                })
        elif cls == "Association":
            kinds = sorted(b.get("class") if b else "?" for b in (sb, tb))
            kinds_set = set(kinds)
            hosts = set(geo.FLOW_NODES) | {"Pool"}
            ok = ("?" not in kinds_set
                  and len(kinds_set) == 2
                  and len(kinds_set & set(geo.ARTIFACTS)) == 1
                  and kinds_set <= (set(geo.ARTIFACTS) | hosts))
            if not ok:
                findings.append({
                    "rule_id": "R4", "element_ids": [eid], "field": "class",
                    "message": (f"Association {eid} connects {kinds[0]}-{kinds[1]}; an "
                                f"association must anchor exactly one artifact "
                                f"(DataObject/DataStore/TextAnnotation) to one flow node or pool. "
                                f"Either the class or an endpoint is wrong."),
                })
    return findings


def _crop_with_labels(image_path, bbox, marks, out_path):
    from image_tools import annotate_region
    return annotate_region(image_path, bbox, marks, out_path, scale=2.0)


def _ask_line(crop_path, call_model_func, question):
    from file_utils import detect_image_mime, image_to_base64
    mime = detect_image_mime(crop_path)
    content = [
        {"type": "text", "text": question},
        {"type": "image_url",
         "image_url": {"url": f"data:{mime};base64,{image_to_base64(crop_path)}"}},
    ]
    try:
        raw, _ = call_model_func([{"role": "system", "content": LINE_CLASSIFY_SYSTEM},
                                  {"role": "user", "content": content}])
        return json.loads(re.sub(r"^```(?:json)?|```$", "", (raw or "").strip(),
                                 flags=re.MULTILINE).strip())
    except Exception as e:
        return {"error": str(e)}


def visual_line_checks(envelope, image_path, call_model_func, budget=4,
                       output_dir=None, base_name=None):
    items = dict(geo.iter_elements(envelope))
    findings, tool_log = [], []
    if not image_path or not os.path.exists(image_path):
        return findings, tool_log
    crops_dir = os.path.join(output_dir, CROP_SUBDIR) if output_dir else None
    used = 0

    orphans = geo.orphan_endpoints(envelope, tol=18.0)
    for op in orphans:
        if used >= budget:
            break
        used += 1
        px, py = op["point"]
        near = geo.boxes_near(envelope, (px, py), radius=140)[:4]
        marks = [{"box": geo.box_of(b), "label": f"{eid}:{(b.get('name') or eid)[:16]}",
                  "color": "#185FA5"} for eid, b, _d in near if geo.box_of(b)]
        bbox = (px - 150, py - 110, 300, 220)
        out_png = None
        if crops_dir:
            out_png = os.path.join(crops_dir, f"{base_name}_orphan_{op['flow']}_{op['role']}.png")
        r = _crop_with_labels(image_path, bbox, marks, out_png or
                              os.path.join(crops_dir or ".", "tmp_orphan.png"))
        if not r.ok:
            tool_log.append({"tool": "annotate_region", "ok": False, "reason": r.reason,
                             "trigger": f"orphan {op['flow']}.{op['role']}"})
            continue
        labels = ", ".join(m["label"] for m in marks) or "(none nearby)"
        q = (f"The arrow/line end near the center of this crop is a loose end of flow "
             f"{op['flow']}. Which labeled box does it actually connect to (or does it leave "
             f"the crop)? Answer JSON only.")
        verdict = _ask_line(r.path, call_model_func, q)
        tool_log.append({"tool": "annotate_region+ask", "ok": "error" not in verdict,
                         "trigger": f"orphan {op['flow']}.{op['role']}",
                         "verdict": verdict})
        if verdict.get("line_present") and verdict.get("to_label"):
            findings.append({
                "rule_id": "R15", "element_ids": [op["flow"]], "field": "end",
                "message": (f"Loose endpoint of {op['flow']} ({op['role']}) actually lands on "
                            f"'{verdict['to_label']}' (visible in crop {os.path.basename(r.path)}); "
                            f"fix the endpoint reference/coordinates accordingly."),
            })

    for eid, body in geo.iter_elements(envelope):
        if used >= budget:
            break
        if body.get("class") != "Association":
            continue
        used += 1
        s, t = geo.box_of(items.get(body.get("sourceRef"))), \
            geo.box_of(items.get(body.get("targetRef")))
        union = geo.union_boxes([b for b in (s, t) if b])
        if union is None:
            continue
        out_png = None
        if crops_dir:
            out_png = os.path.join(crops_dir, f"{base_name}_assoc_{eid}.png")
        r = _crop_with_labels(image_path, union, [], out_png or
                              os.path.join(crops_dir or ".", "tmp_assoc.png"))
        if not r.ok:
            tool_log.append({"tool": "crop_zoom", "ok": False, "reason": r.reason,
                             "trigger": f"assoc {eid}"})
            continue
        q = ("Between the marked shapes there should be a line. Is it DOTTED/DASHED "
             "(association) or SOLID with a filled arrowhead (sequence flow)? JSON only.")
        verdict = _ask_line(r.path, call_model_func, q)
        tool_log.append({"tool": "crop_zoom+ask", "ok": "error" not in verdict,
                         "trigger": f"assoc {eid}", "verdict": verdict})
        if verdict.get("line_present") and verdict.get("line_style") == "solid":
            findings.append({
                "rule_id": "R4", "element_ids": [eid], "field": "class",
                "message": (f"Line {eid} appears SOLID in the crop "
                            f"({os.path.basename(r.path)}); if it is a control-flow line, "
                            f"change its class to SequenceFlow (and fix endpoints)."),
            })

    return findings, tool_log


def assemble_findings(elements_text, inventory_errors=None, edge_errors=None,
                      json_issues=None):
    findings = []
    seen = set()
    for f in (inventory_errors or []) + (edge_errors or []):
        key = (f.get("rule_id"), tuple(f.get("element_ids") or []),
               f.get("message", "")[:60])
        if key not in seen:
            findings.append(dict(f))
            seen.add(key)
    for issue in json_issues or []:
        if issue["severity"] != "error":
            continue
        findings.append({
            "rule_id": issue["rule_id"], "element_ids": issue["element_ids"],
            "field": issue["layer"],
            "message": issue["message"] + (f" Fix hint: {issue['fix_hint']}"
                                           if issue["fix_hint"] else ""),
        })
    return findings


def prepare_findings(elements_text, image_path, call_model_func, ablation=None,
                     inventory_errors=None, edge_errors=None,
                     output_dir=None, base_name=None):
    ablation = ablation or {}
    info = {"findings": [], "tool_log": [], "class_consistency": []}
    try:
        envelope = json.loads(elements_text) if isinstance(elements_text, str) \
            else elements_text
    except Exception as e:
        print(f"    [review-tools] JSON 解析失败，只注入 B/C 合成错误: {e}")
        findings = assemble_findings(elements_text, inventory_errors, edge_errors)
        info["findings"] = findings
        return findings, info

    findings = assemble_findings(envelope, inventory_errors, edge_errors,
                                 json_issues=tc.run_json_checks(envelope))

    if ablation.get("review_tools", True):
        cc = class_consistency_checks(envelope)
        info["class_consistency"] = cc
        findings.extend(cc)
        budget = int(ablation.get("max_tool_calls", 8))
        used_by_upstream = min(budget, len(inventory_errors or []) + len(edge_errors or []))
        vfindings, tool_log = visual_line_checks(
            envelope, image_path, call_model_func,
            budget=max(0, budget - used_by_upstream),
            output_dir=output_dir, base_name=base_name)
        findings.extend(vfindings)
        info["tool_log"] = tool_log

    info["findings"] = findings
    if findings:
        print(f"    [review-tools] 工具层产出确定性 findings {len(findings)} 条（供本次审查使用）")
    if output_dir and base_name:
        save_json(output_dir, OUTPUT_SUBDIR,
                  f"{base_name}_step2a_review_tools.json", info)
    return findings, info


def findings_to_text(findings):
    if not findings:
        return ""
    lines = ["## Deterministic pre-check findings (produced by code; verify against the image)"]
    for f in findings:
        lines.append(f"- [rule_id={f.get('rule_id')}] element_ids={f.get('element_ids')} "
                     f"field={f.get('field')}: {f.get('message')}")
    return "\n".join(lines) + "\n"


def self_test():
    env = {"elements": [
        {"A": {"class": "Activity", "x": 0, "y": 0, "width": 100, "height": 50}},
        {"B": {"class": "Activity", "x": 300, "y": 0, "width": 100, "height": 50}},
        {"D": {"class": "DataObject", "x": 0, "y": 200, "width": 40, "height": 50}},
        {"F1": {"class": "SequenceFlow", "sourceRef": "A", "targetRef": "D"}},
        {"F2": {"class": "Association", "sourceRef": "B", "targetRef": "A"}},
        {"F3": {"class": "Association", "sourceRef": "B", "targetRef": "D"}},
    ]}
    cc = class_consistency_checks(env)
    ids = {f["element_ids"][0] for f in cc}
    assert "F1" in ids and "F2" in ids, cc
    assert "F3" not in ids, f"合法 Association 被误报: {[f for f in cc if f['element_ids'] == ['F3']]}"
    findings = assemble_findings(env, inventory_errors=[
        {"rule_id": "RC1", "element_ids": [], "field": "DataObject", "message": "missing 1"},
    ], json_issues=tc.run_json_checks(env))
    findings.extend(class_consistency_checks(env))
    assert any(f["rule_id"] == "RC1" for f in findings)
    assert any(f["rule_id"] == "R4" for f in findings)
    txt = findings_to_text(findings)
    assert "Deterministic pre-check findings" in txt and "RC1" in txt
    print("review_tools self-test OK")


if __name__ == "__main__":
    self_test()
