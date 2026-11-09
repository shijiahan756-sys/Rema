from __future__ import annotations

import json
import math
import os
import re

import bpmn_geometry as geo
from config import CROP_SUBDIR, OUTPUT_SUBDIR
from file_utils import save_json

MAX_ROUNDS = 12

ADJUDICATE_SYSTEM = """You are a strict visual verifier answering ONE local question about a cropped,
magnified region of a hand-drawn BPMN diagram. The two shapes of interest are marked with labeled boxes.
Answer ONLY with a JSON object, no prose:
{"connected": true|false, "direction": "A->B"|"B->A"|"none",
 "line_style": "solid"|"dotted"|"dashed"|"none", "confidence": 0.0-1.0}
Rules:
- "connected" means a drawn line between the two marked shapes is visible in THIS crop.
- Judge ONLY what is visible; do not use BPMN common sense to fill gaps.
- If the line exits the crop, say connected=false and confidence<=0.3."""


def _by_id(envelope):
    return dict(geo.iter_elements(envelope))


def _pool_map(envelope):
    pools = set()
    lanes = {}
    contains = {}
    for eid, body in geo.iter_elements(envelope):
        cls = body.get("class")
        if cls == "Pool":
            pools.add(eid)
            contains[eid] = [c for c in (body.get("contains") or []) if isinstance(c, str)]
        elif cls == "Lane":
            lanes[eid] = (body.get("parent"), [c for c in (body.get("contains") or [])
                                               if isinstance(c, str)])
    node_pool = {}
    for pid, members in contains.items():
        for m in members:
            node_pool[m] = pid
    for _lid, (parent, members) in lanes.items():
        for m in members:
            node_pool.setdefault(m, parent)
    for nid, body in geo.iter_elements(envelope):
        if nid not in node_pool and body.get("parent"):
            node_pool[nid] = body["parent"]
    return node_pool


def _existing_flow_pairs(envelope):
    pairs = set()
    for _eid, body in geo.iter_elements(envelope):
        if body.get("class") == "SequenceFlow":
            pairs.add((body.get("sourceRef"), body.get("targetRef")))
    return pairs


def _legal_sequence_pair(env_items, src, tgt, node_pool, existing):
    if src == tgt or (src, tgt) in existing:
        return False, "duplicate_or_self"
    sb, tb = env_items.get(src), env_items.get(tgt)
    if not sb or not tb:
        return False, "missing_endpoint"
    if tb.get("class") == "Event" and (tb.get("subType") == "StartEvent"):
        return False, "flow_into_start"
    if sb.get("class") == "Event" and (sb.get("subType") == "EndEvent"):
        return False, "flow_out_of_end"
    ps, pt = node_pool.get(src), node_pool.get(tgt)
    if ps and pt and ps != pt:
        return False, "cross_pool"
    return True, ""


def generate_edge_candidates(envelope, dangling, k=3):
    items = _by_id(envelope)
    node_pool = _pool_map(envelope)
    existing = _existing_flow_pairs(envelope)
    deg = geo.node_degrees(envelope)
    orphans = geo.orphan_endpoints(envelope)
    candidates = []
    for nid, info in dangling.items():
        need = info.get("detail") or ([info["missing"]] if info.get("missing") else [])
        me_box = geo.box_of(items.get(nid))
        if me_box is None:
            continue
        me_center = geo.center_of(items[nid])
        for missing in need:
            if missing in ("start_has_incoming", "end_has_outgoing"):
                continue
            for oid, obody in items.items():
                if oid == nid or obody.get("class") not in geo.FLOW_NODES:
                    continue
                if missing == "incoming":
                    src, tgt = oid, nid
                else:
                    src, tgt = nid, oid
                ok, reason = _legal_sequence_pair(items, src, tgt, node_pool, existing)
                if not ok:
                    continue
                obox = geo.box_of(obody)
                gap = geo.box_gap(me_box, obox) if obox else 1e9
                same_pool = node_pool.get(nid) == node_pool.get(oid)
                deg_bonus = 0.0
                if missing == "incoming":
                    deg_bonus = -0.5 * len(deg.get(oid, {}).get("out", []))
                else:
                    deg_bonus = -0.5 * len(deg.get(oid, {}).get("in", []))
                orphan_bonus = 0.0
                for op in orphans:
                    oc = geo.center_of(obody)
                    if oc and math.hypot(op["point"][0] - oc[0], op["point"][1] - oc[1]) < 60:
                        orphan_bonus += 1.0
                score = -gap / 100.0 + (0.5 if same_pool else 0.0) + deg_bonus + orphan_bonus
                candidates.append({
                    "kind": "sequence",
                    "missing": missing,
                    "node": nid,
                    "source": src, "target": tgt,
                    "score": round(score, 3),
                    "gap": round(gap, 1),
                    "same_pool": same_pool,
                })
    best = {}
    for c in candidates:
        key = (c["node"], c["missing"])
        best.setdefault(key, []).append(c)
    out = []
    for key, lst in best.items():
        lst.sort(key=lambda c: -c["score"])
        out.extend(lst[:k])
    out.sort(key=lambda c: -c["score"])
    return out


def adjudicate_candidate(image_path, candidate, items, call_model_func, out_png=None):
    from image_tools import crop_zoom
    src, tgt = candidate["source"], candidate["target"]
    a, b = geo.box_of(items.get(src)), geo.box_of(items.get(tgt))
    if a is None or b is None:
        return None
    union = geo.union_boxes([a, b])
    crop_path = out_png
    if not crop_path:
        import tempfile
        crop_path = os.path.join(tempfile.gettempdir(),
                                 f"adj_{src}_{tgt}.png")
    r = crop_zoom(image_path, union, crop_path, scale=2.0)
    if not r.ok:
        print(f"    [edge] 裁剪失败: {r.reason}")
        return None
    la = items[src].get("name") or src
    lb = items[tgt].get("name") or tgt
    text = (f"Shape A (box labeled 'A: {la}') and Shape B (box labeled 'B: {lb}').\n"
            "Question: is there a DIRECTED SOLID line with an arrowhead from A to B in this crop?\n"
            "Answer with the JSON object only.")
    content = [
        {"type": "text", "text": text},
        {"type": "image_url",
         "image_url": {"url": "file:///" + os.path.abspath(crop_path).replace("\\", "/")
                       if False else None}},
    ]
    from file_utils import detect_image_mime, image_to_base64
    mime = detect_image_mime(crop_path)
    content[1] = {"type": "image_url",
                  "image_url": {"url": f"data:{mime};base64,{image_to_base64(crop_path)}"}}
    try:
        raw, _ = call_model_func([{"role": "system", "content": ADJUDICATE_SYSTEM},
                                  {"role": "user", "content": content}])
        verdict = json.loads(re.sub(r"^```(?:json)?|```$", "", (raw or "").strip(),
                                    flags=re.MULTILINE).strip())
        return verdict if isinstance(verdict, dict) else None
    except Exception as e:
        print(f"    [edge] 裁决调用失败: {e}")
        return None


def _add_flow(envelope, src, tgt, anchor_note):
    items = _by_id(envelope)
    n = 0
    for eid, _ in geo.iter_elements(envelope):
        m = re.fullmatch(r"Object(\d+)", str(eid))
        if m:
            n = max(n, int(m.group(1)))
    n += 1
    fid = f"Object{n}"
    a = geo.box_of(items[src])
    b = geo.box_of(items[tgt])
    p_s = geo.anchor_point(a, geo.center_of(items[tgt])) if a and b else None
    p_e = geo.anchor_point(b, geo.center_of(items[src])) if a and b else None
    body = {
        "class": "SequenceFlow",
        "sourceRef": src, "targetRef": tgt,
        "origin": "inferred",
    }
    if p_s:
        body["start"] = {"x": round(p_s[0]), "y": round(p_s[1])}
    if p_e:
        body["end"] = {"x": round(p_e[0]), "y": round(p_e[1])}
    if anchor_note:
        body["note"] = str(anchor_note)[:120]
    envelope.setdefault("elements", []).append({fid: body})
    return fid


def _unanchored_artifacts(envelope):
    items = dict(geo.iter_elements(envelope))
    anchored = set()
    for _eid, body in items.items():
        if body.get("class") == "Association":
            for ref in (body.get("sourceRef"), body.get("targetRef")):
                if ref:
                    anchored.add(ref)
    out = {}
    for eid, body in items.items():
        if body.get("class") == "DataObject" and eid not in anchored:
            out[eid] = body
    return out


def generate_association_candidates(envelope, k=2):
    items = _by_id(envelope)
    unanchored = _unanchored_artifacts(envelope)
    hosts = [(eid, b) for eid, b in items.items() if b.get("class") in
             ("Activity", "Gateway", "Event")]
    cands = []
    for aid, abody in unanchored.items():
        abox = geo.box_of(abody)
        if abox is None:
            continue
        scored = []
        for hid, hbody in hosts:
            hbox = geo.box_of(hbody)
            if hbox is None:
                continue
            gap = geo.box_gap(abox, hbox)
            if gap > 400:
                continue
            bonus = -gap / 100.0 + (0.8 if hbody.get("class") == "Activity" else 0.0)
            scored.append({"kind": "association", "artifact": aid, "host": hid,
                           "source": aid, "target": hid,
                           "score": round(bonus, 3), "gap": round(gap, 1)})
        scored.sort(key=lambda c: -c["score"])
        cands.extend(scored[:k])
    cands.sort(key=lambda c: -c["score"])
    return cands


def _add_association(envelope, artifact_id, host_id):
    items = _by_id(envelope)
    n = 0
    for eid, _ in geo.iter_elements(envelope):
        m = re.fullmatch(r"Object(\d+)", str(eid))
        if m:
            n = max(n, int(m.group(1)))
    n += 1
    fid = f"Object{n}"
    a, b = geo.box_of(items[artifact_id]), geo.box_of(items[host_id])
    body = {"class": "Association", "sourceRef": artifact_id, "targetRef": host_id,
            "arrow": False, "origin": "inferred"}
    if a and b:
        p1 = geo.anchor_point(a, geo.center_of(items[host_id]))
        p2 = geo.anchor_point(b, geo.center_of(items[artifact_id]))
        body["start"] = {"x": round(p1[0]), "y": round(p1[1])}
        body["end"] = {"x": round(p2[0]), "y": round(p2[1])}
    envelope.setdefault("elements", []).append({fid: body})
    return fid


def run_edge_completion(initial_elements, image_path, call_model_func,
                        ablation=None, output_dir=None, base_name=None):
    ablation = ablation or {}
    info = {"dangling_before": {}, "dangling_after": {}, "added_flows": [],
            "added_assoc": [], "rejected": [], "adjudications": []}
    try:
        envelope = json.loads(initial_elements) if isinstance(initial_elements, str) \
            else initial_elements
    except Exception as e:
        print(f"    [edge] JSON 解析失败，跳过边补全: {e}")
        return initial_elements, [], info

    do_seq = ablation.get("edge_completion", True)
    do_assoc = ablation.get("edge_completion", True) and ablation.get("supporting_pass", True)
    if not do_seq and not do_assoc:
        return initial_elements, [], info

    items = _by_id(envelope)
    dangling = geo.dangling_nodes(envelope)
    info["dangling_before"] = {k: v["missing"] for k, v in dangling.items()}
    if dangling:
        print("    [edge] 悬空节点: " + ", ".join(
            f"{k}({v['name'] or v['class']}) 缺 {v['missing']}" for k, v in dangling.items()))

    crops_dir = os.path.join(output_dir, CROP_SUBDIR) if output_dir else None

    if do_seq and dangling:
        cands = generate_edge_candidates(envelope, dangling,
                                         k=int(ablation.get("edge_candidates", 3)))
        print(f"    [edge] 候选 {len(cands)} 个，逐对裁决（最多 {MAX_ROUNDS} 次）...")
        for i, cand in enumerate(cands[:MAX_ROUNDS]):
            out_png = None
            if crops_dir:
                out_png = os.path.join(crops_dir,
                                       f"{base_name}_adj_{cand['source']}_{cand['target']}.png")
            verdict = adjudicate_candidate(image_path, cand, items, call_model_func,
                                           out_png=out_png)
            rec = {"candidate": {k: cand[k] for k in ("source", "target", "missing")},
                   "verdict": verdict}
            info["adjudications"].append(rec)
            if verdict and verdict.get("connected"):
                direction = verdict.get("direction", "A->B")
                src, tgt = cand["source"], cand["target"]
                if direction == "B->A":
                    src, tgt = tgt, src
                ok, _ = _legal_sequence_pair(items, src, tgt, _pool_map(envelope),
                                             _existing_flow_pairs(envelope))
                if ok:
                    fid = _add_flow(envelope, src, tgt, verdict.get("line_style"))
                    items = _by_id(envelope)
                    info["added_flows"].append({"id": fid, "source": src, "target": tgt})
                    print(f"    [edge] 裁决=有 → 补线 {fid}: {src} -> {tgt}")
            else:
                info["rejected"].append({k: cand[k] for k in ("source", "target", "missing")})

    if do_assoc:
        acands = generate_association_candidates(envelope)
        if acands:
            print(f"    [edge] 未锚定 artifact 的关联候选 {len(acands)} 个...")
        for cand in acands[:MAX_ROUNDS]:
            out_png = None
            if crops_dir:
                out_png = os.path.join(crops_dir,
                                       f"{base_name}_assoc_{cand['artifact']}_{cand['host']}.png")
            verdict = adjudicate_candidate(image_path, cand, items, call_model_func,
                                           out_png=out_png)
            info["adjudications"].append({"candidate": {
                "source": cand["source"], "target": cand["target"], "missing": "association"},
                "verdict": verdict})
            if verdict and verdict.get("connected") and \
                    verdict.get("line_style") in ("dotted", "dashed", None, ""):
                fid = _add_association(envelope, cand["artifact"], cand["host"])
                items = _by_id(envelope)
                info["added_assoc"].append({"id": fid, "artifact": cand["artifact"],
                                            "host": cand["host"]})
                print(f"    [edge] 裁决=有点线 → 补关联 {fid}: {cand['artifact']} - {cand['host']}")

    dangling_after = geo.dangling_nodes(envelope)
    info["dangling_after"] = {k: v["missing"] for k, v in dangling_after.items()}

    synthetic = []
    for nid, v in dangling_after.items():
        if v["missing"] in ("start_has_incoming", "end_has_outgoing"):
            continue
        synthetic.append({
            "rule_id": "RC2",
            "element_ids": [nid],
            "field": "connectivity",
            "message": (f"Node {nid} ('{v.get('name') or v.get('class')}') is dangling: "
                        f"missing {v['missing']}. Check the image area around it for an "
                        f"unrecognized line and add the corresponding flow, or justify "
                        f"(e.g. the diagram itself is incomplete there)."),
        })
    info["synthetic_errors"] = synthetic

    if output_dir and base_name:
        save_json(output_dir, OUTPUT_SUBDIR,
                  f"{base_name}_step0c_edge_completion.json", info)
    return json.dumps(envelope, ensure_ascii=False, indent=2), synthetic, info


def self_test():
    env = {"elements": [
        {"P": {"class": "Pool", "x": 0, "y": 0, "width": 600, "height": 300,
               "contains": ["L1"]}},
        {"L1": {"class": "Lane", "parent": "P", "x": 0, "y": 0, "width": 600, "height": 300,
                "contains": ["A", "B", "C"]}},
        {"A": {"class": "Activity", "x": 0, "y": 0, "width": 100, "height": 50}},
        {"B": {"class": "Activity", "x": 300, "y": 0, "width": 100, "height": 50}},
        {"C": {"class": "Activity", "x": 320, "y": 200, "width": 100, "height": 50}},
        {"F1": {"class": "SequenceFlow", "sourceRef": "A", "targetRef": "B"}},
    ]}
    dang = geo.dangling_nodes(env)
    assert set(dang) == {"A", "B", "C"}, dang
    cands = generate_edge_candidates(env, dang, k=3)
    keys = {(c["source"], c["target"]) for c in cands}
    assert ("B", "A") in keys or ("C", "A") in keys, keys
    assert any(c["source"] == "B" and c["target"] == "C" for c in cands), cands
    env2 = {"elements": [
        {"S": {"class": "Event", "subType": "StartEvent", "x": 0, "y": 0,
               "width": 36, "height": 36}},
        {"A": {"class": "Activity", "x": 200, "y": 0, "width": 100, "height": 50}},
    ]}
    c2 = generate_edge_candidates(env2, geo.dangling_nodes(env2), k=5)
    assert not any(c["target"] == "S" for c in c2), c2
    env3 = {"elements": [
        {"A": {"class": "Activity", "x": 0, "y": 0, "width": 100, "height": 50}},
        {"D": {"class": "DataObject", "x": 150, "y": 0, "width": 40, "height": 50}},
    ]}
    ac = generate_association_candidates(env3, k=2)
    assert ac and ac[0]["artifact"] == "D" and ac[0]["host"] == "A"
    print("edge_completion self-test OK")


if __name__ == "__main__":
    self_test()
