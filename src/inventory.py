from __future__ import annotations

import json
import os
import re

import bpmn_geometry as geo
from config import CROP_SUBDIR, OUTPUT_SUBDIR
from file_utils import detect_image_mime, get_image_size, image_to_base64, save_json

SUPPORTING_CLASSES = ("DataObject", "Association", "MessageFlow")
INVENTORY_CLASSES = ("Pool", "Lane", "Event", "Activity", "Gateway",
                     "DataObject", "SequenceFlow", "MessageFlow", "Association")

INVENTORY_SYSTEM = """You are a surveyor, NOT an extractor. Your ONLY job is to report WHAT KIND of BPMN
objects are visible and HOW MANY of each, plus a rough bounding box per instance.
Do NOT read labels, do NOT judge line styles beyond solid-vs-dotted-vs-dashed, do NOT output attributes.
Output ONE JSON object, no prose:
{"cells":[{"row":0,"col":0,
   "counts":{"Activity":2,"Event":1,...},
   "instances":[{"class":"Activity","bbox":[x,y,w,h],"note":"box with text inside"}...]}]}
Rules:
- bbox is in the coordinate frame of the FULL original image (the cell origin offset is given in the prompt).
- Count visibly distinct instances. If a dotted/dashed line clearly connects two objects, count it once as Association.
- When unsure whether a mark is an element, still report it with note "unsure".
- If a cell contains no elements, emit it with empty counts."""

REGION_SYSTEM = """You extract BPMN elements from a CROPPED, MAGNIFIED region of a larger hand-drawn diagram.
Output the SAME JSON envelope used by the main extractor ({"elements":[{ObjectN:{...}}]}).
Coordinates: the crop's top-left corner in the ORIGINAL image is (ox, oy). Output x/y/width/height in
ORIGINAL image pixels: original_x = ox + crop_x. Same rule for connection start/end points.
Only report elements clearly inside the crop; mark connection endpoints with their coordinates even if
the other end is outside the crop. Do not invent elements."""


def _cell_boxes(width, height, grid):
    n = max(1, int(grid))
    cw, ch = width / n, height / n
    cells = []
    for r in range(n):
        for c in range(n):
            cells.append({"row": r, "col": c,
                          "x": int(c * cw), "y": int(r * ch),
                          "w": int(cw + (1 if c == n - 1 else 0)),
                          "h": int(ch + (1 if r == n - 1 else 0))})
    return cells


def build_inventory_messages(image_path, grid=2):
    width, height = get_image_size(image_path)
    cells = _cell_boxes(width, height, grid)
    mime = detect_image_mime(image_path)
    cell_desc = "; ".join(
        f"row{c['row']}col{c['col']}=x[{c['x']},{c['x'] + c['w']}) y[{c['y']},{c['y'] + c['h']})"
        for c in cells)
    text = (
        "## Task\n"
        "Survey the attached hand-drawn BPMN diagram. Report per-cell counts and one rough bbox per instance.\n"
        f"Image size: {width}x{height} px. Cell layout ({grid}x{grid}): {cell_desc}\n"
        "Classes to count (use exactly these keys when non-zero): "
        f"{', '.join(INVENTORY_CLASSES)}.\n"
        "Pay special attention to small, low-contrast objects: document/data-store icons (both are DataObject), "
        "dotted (association) lines and dashed cross-pool (message flow) lines.\n\n"
        "## Output\nOne JSON object as specified in the system prompt. No prose, no Markdown."
    )
    content = [
        {"type": "text", "text": text},
        {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{image_to_base64(image_path)}"}},
    ]
    return [{"role": "system", "content": INVENTORY_SYSTEM},
            {"role": "user", "content": content}]


def _parse_json_loose(raw):
    if raw is None:
        raise ValueError("empty model response")
    txt = raw.strip()
    txt = re.sub(r"^```(?:json)?|```$", "", txt, flags=re.MULTILINE).strip()
    start = txt.find("{")
    end = txt.rfind("}")
    if start >= 0 and end > start:
        txt = txt[start:end + 1]
    return json.loads(txt)


def aggregate_inventory(parsed):
    counts, instances = {}, []
    for cell in parsed.get("cells", []):
        if not isinstance(cell, dict):
            continue
        for cls, n in (cell.get("counts") or {}).items():
            if cls in INVENTORY_CLASSES:
                try:
                    counts[cls] = counts.get(cls, 0) + int(n)
                except (TypeError, ValueError):
                    pass
        for inst in cell.get("instances") or []:
            if not isinstance(inst, dict) or inst.get("class") not in INVENTORY_CLASSES:
                continue
            bb = inst.get("bbox") or inst.get("box")
            if isinstance(bb, (list, tuple)) and len(bb) == 4:
                instances.append({"class": inst["class"], "bbox": [float(v) for v in bb],
                                  "note": str(inst.get("note", ""))[:120]})
    for inst in instances:
        counts.setdefault(inst["class"], 0)
    return {"counts": counts, "instances": instances}


def run_inventory(image_path, call_model_func, grid=2, output_dir=None, base_name=None):
    try:
        msgs = build_inventory_messages(image_path, grid)
        raw, _ = call_model_func(msgs)
        parsed = _parse_json_loose(raw)
        inv = aggregate_inventory(parsed)
        inv["grid"] = int(grid)
        inv["image"] = os.path.basename(image_path)
        if output_dir and base_name:
            save_json(output_dir, OUTPUT_SUBDIR,
                      f"{base_name}_step0_inventory.json", inv)
        return inv
    except Exception as e:
        print(f"    [inventory] 盘点失败（按跳过处理）: {e}")
        return None


def diff_inventory(inventory, elements_envelope):
    found = {}
    for _eid, body in geo.iter_elements(elements_envelope):
        cls = body.get("class")
        if cls in INVENTORY_CLASSES:
            found[cls] = found.get(cls, 0) + 1
    disc, summary = [], {"classes": {}, "zero_tolerance_hits": 0}
    if not inventory:
        return disc, summary
    inv_counts = dict(inventory.get("counts") or {})
    extracted = geo.iter_elements(elements_envelope)
    for cls in INVENTORY_CLASSES:
        expected = int(inv_counts.get(cls, 0))
        got = int(found.get(cls, 0))
        entry = {"expected": expected, "found": got}
        if expected > got:
            missing = expected - got
            entry["missing"] = missing
            inv_boxes = [i for i in inventory.get("instances", []) if i["class"] == cls]
            ext_boxes = []
            for eid, body in extracted:
                if body.get("class") == cls:
                    ext_boxes.append(geo.box_of(body))
            unmatched = []
            for ib in inv_boxes:
                bb = geo.box_of({"x": ib["bbox"][0], "y": ib["bbox"][1],
                                 "width": ib["bbox"][2], "height": ib["bbox"][3]})
                if bb is None:
                    unmatched.append(ib)
                    continue
                best = max((geo.iou(bb, eb) for eb in ext_boxes if eb), default=0.0)
                if best < 0.3:
                    unmatched.append(ib)
            entry["unmatched_boxes"] = unmatched[:6]
            zero = cls in SUPPORTING_CLASSES
            entry["zero_tolerance"] = zero
            if zero or missing > 1:
                disc.append({"class": cls, "expected": expected, "found": got,
                             "missing": missing, "zero_tolerance": zero,
                             "unmatched_boxes": entry["unmatched_boxes"],
                             "rule_id": "RC1"})
                if zero:
                    summary["zero_tolerance_hits"] += 1
        elif expected < got:
            entry["over_extracted"] = got - expected
        summary["classes"][cls] = entry
    return disc, summary


def run_region_extraction(image_path, bbox, call_model_func, margin_ratio=0.6,
                          out_png=None, name_hint="region"):
    from image_tools import crop_zoom
    width, height = get_image_size(image_path)
    box = geo.expand_box(tuple(bbox), margin_ratio=margin_ratio,
                         width=width, height=height)
    if out_png:
        crop = crop_zoom(image_path, box, out_png, scale=2.0)
        if not crop.ok:
            print(f"    [region] 裁剪失败: {crop.reason}")
    mime = detect_image_mime(image_path)
    crop_png = out_png or os.path.join(tempfile_dir(), f"{name_hint}.png")
    if not os.path.exists(crop_png):
        r = crop_zoom(image_path, box, crop_png, scale=2.0)
        if not r.ok:
            return None, f"crop failed: {r.reason}"
    text = (
        "## Crop geometry\n"
        f"Crop bbox in original image: x={box[0]:.0f}, y={box[1]:.0f}, w={box[2]:.0f}, h={box[3]:.0f}\n"
        "Output coordinates in ORIGINAL image pixels (ox = "
        f"{box[0]:.0f}, oy = {box[1]:.0f}).\n\n"
        "## Output\nThe elements envelope only."
    )
    content = [
        {"type": "text", "text": text},
        {"type": "image_url",
         "image_url": {"url": f"data:{mime};base64,{image_to_base64(crop_png)}"}},
    ]
    try:
        raw, _ = call_model_func([{"role": "system", "content": REGION_SYSTEM},
                                  {"role": "user", "content": content}])
        parsed = _parse_json_loose(raw)
        if "elements" not in parsed:
            raise ValueError("envelope missing")
        return parsed, None
    except Exception as e:
        return None, f"region extraction failed: {e}"


def tempfile_dir():
    import tempfile
    return tempfile.gettempdir()


def merge_elements(main_envelope, region_envelope, iou_thresh=0.5):
    import copy
    merged = copy.deepcopy(main_envelope)
    items = merged.setdefault("elements", [])
    existing = {}
    max_n = 0
    for entry in items:
        if not isinstance(entry, dict) or not entry:
            continue
        eid, body = next(iter(entry.items()))
        m = re.fullmatch(r"Object(\d+)", str(eid))
        if m:
            max_n = max(max_n, int(m.group(1)))
        if body.get("class") in geo.SHAPES:
            existing.setdefault(body["class"], []).append(geo.box_of(body))
    added = []
    for entry in (region_envelope or {}).get("elements", []):
        if not isinstance(entry, dict) or not entry:
            continue
        _old_id, body = next(iter(entry.items()))
        cls = body.get("class")
        bb = geo.box_of(body)
        if cls in geo.SHAPES and bb is not None:
            dup = any(geo.iou(bb, eb) >= iou_thresh for eb in existing.get(cls, []) if eb)
            if dup:
                continue
            existing.setdefault(cls, []).append(bb)
        max_n += 1
        new_id = f"Object{max_n}"
        body = dict(body)
        body.setdefault("origin", "extracted")
        items.append({new_id: body})
        added.append(new_id)
    return merged, added


def run_completeness_pass(initial_elements, image_path, call_model_func,
                          ablation=None, output_dir=None, base_name=None):
    ablation = ablation or {}
    info = {"inventory": None, "discrepancies": [], "rescans": [], "merged": []}
    if not ablation.get("inventory_check", True):
        return initial_elements, [], info
    if not ablation.get("supporting_pass", True):
        pass

    grid = int(ablation.get("inventory_grid", 2))
    print(f"    [inventory] 分块盘点 grid={grid} ...")
    inv = run_inventory(image_path, call_model_func, grid=grid,
                        output_dir=output_dir, base_name=base_name)
    info["inventory"] = inv
    if inv is None:
        return initial_elements, [], info

    try:
        envelope = json.loads(initial_elements) if isinstance(initial_elements, str) \
            else initial_elements
    except Exception as e:
        print(f"    [inventory] 元素 JSON 解析失败，跳过差集: {e}")
        return initial_elements, [], info

    disc, summary = diff_inventory(inv, envelope)
    info["discrepancies"] = disc
    if disc:
        print("    [inventory] 差集: " + ", ".join(
            f"{d['class']} 缺 {d['missing']}" + ("（零容忍）" if d["zero_tolerance"] else "")
            for d in disc))
    else:
        print("    [inventory] 差集=0（提取层没有可观测遗漏）")

    crops_dir = os.path.join(output_dir, CROP_SUBDIR) if output_dir else None
    rescans = 0
    for d in disc:
        if rescans >= 4:
            break
        if not (d["zero_tolerance"] or d["missing"] > 1):
            continue
        for ib in d.get("unmatched_boxes", [])[:2]:
            if rescans >= 4:
                break
            rescans += 1
            out_png = None
            if crops_dir:
                out_png = os.path.join(crops_dir,
                                       f"{base_name}_rescan_{d['class']}_{rescans}.png")
            region_env, err = run_region_extraction(
                image_path, ib["bbox"], call_model_func, out_png=out_png,
                name_hint=f"{base_name}_{d['class']}_{rescans}")
            info["rescans"].append({"class": d["class"], "bbox": ib["bbox"],
                                    "ok": region_env is not None, "error": err})
            if region_env:
                envelope, added = merge_elements(envelope, region_env)
                info["merged"].extend(added)
                if added:
                    print(f"    [inventory] 区域重扫并入 {len(added)} 个元素: {added}")

    disc2, _ = diff_inventory(inv, envelope)
    synthetic = []
    for d in disc2:
        if not (d["zero_tolerance"] or d["missing"] > 1):
            continue
        boxes = d.get("unmatched_boxes") or []
        loc = f" near bbox {boxes[0]['bbox']}" if boxes else ""
        synthetic.append({
            "rule_id": "RC1",
            "element_ids": [],
            "field": d["class"],
            "message": (f"Inventory counted {d['expected']} {d['class']} but extraction has "
                        f"{d['found']} (missing {d['missing']}){loc}. Re-check that area of the "
                        f"image and add the missing element(s) with realistic position and "
                        f"connections, or explain why the count is wrong."),
        })
    info["synthetic_errors"] = synthetic

    if output_dir and base_name:
        save_json(output_dir, OUTPUT_SUBDIR,
                  f"{base_name}_step0b_completeness.json", info)
    return json.dumps(envelope, ensure_ascii=False, indent=2), synthetic, info


def self_test():
    inv = {"counts": {"Activity": 6, "Event": 2, "Gateway": 1, "DataObject": 5,
                      "Association": 10, "SequenceFlow": 9, "Pool": 1, "Lane": 2},
           "instances": [
               {"class": "Association", "bbox": [10, 10, 50, 30], "note": ""},
               {"class": "Association", "bbox": [100, 10, 50, 30], "note": ""},
           ]}
    env = {"elements": []}
    n = 0
    for cls, cnt in (("Activity", 6), ("Event", 2), ("Gateway", 1), ("DataObject", 5),
                     ("Association", 8), ("SequenceFlow", 9), ("Pool", 1), ("Lane", 2)):
        for _ in range(cnt):
            n += 1
            env["elements"].append({f"Object{n}": {"class": cls, "x": 500, "y": 500,
                                                   "width": 80, "height": 40}})
    disc, summary = diff_inventory(inv, env)
    cls_d = {d["class"]: d for d in disc}
    assert "Association" in cls_d and cls_d["Association"]["missing"] == 2
    assert cls_d["Association"]["zero_tolerance"] is True
    assert summary["zero_tolerance_hits"] == 1
    inv2 = {"counts": {"DataObject": 1}, "instances": [
        {"class": "DataObject", "bbox": [500, 500, 80, 40], "note": ""}]}
    disc2, _ = diff_inventory(inv2, env)
    assert all(d["class"] != "DataObject" for d in disc2), disc2
    main = {"elements": [{"Object1": {"class": "Activity", "x": 0, "y": 0,
                                      "width": 100, "height": 50}}]}
    region = {"elements": [
        {"X1": {"class": "Activity", "x": 2, "y": 2, "width": 100, "height": 50}},
        {"X2": {"class": "DataObject", "x": 300, "y": 0, "width": 40, "height": 50}},
    ]}
    merged, added = merge_elements(main, region)
    assert added == ["Object2"], added
    assert merged["elements"][1]["Object2"]["class"] == "DataObject"
    import io as _io
    print("inventory self-test OK")


if __name__ == "__main__":
    self_test()
