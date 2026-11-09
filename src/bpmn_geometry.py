from __future__ import annotations

import math

FLOW_NODES = ("Activity", "Gateway", "Event")
ARTIFACTS = ("DataObject", "DataStore", "TextAnnotation")
CONNECTIONS = ("SequenceFlow", "MessageFlow", "Association")
SHAPES = ("Pool", "Lane") + FLOW_NODES + ARTIFACTS

ACTIVITY_TAGS = (
    "task", "userTask", "serviceTask", "manualTask", "scriptTask", "sendTask",
    "receiveTask", "businessRuleTask", "callActivity", "subProcess",
)
EVENT_TAGS = (
    "startEvent", "endEvent", "intermediateCatchEvent", "intermediateThrowEvent",
    "boundaryEvent",
)
GATEWAY_TAGS = (
    "exclusiveGateway", "parallelGateway", "inclusiveGateway", "eventBasedGateway",
    "complexGateway",
)
DATA_TAGS = ("dataObject", "dataObjectReference", "dataStoreReference")


def iter_elements(envelope):
    if isinstance(envelope, dict):
        items = envelope.get("elements", [])
    elif isinstance(envelope, list):
        items = envelope
    else:
        items = []
    out = []
    for entry in items or []:
        if not isinstance(entry, dict) or not entry:
            continue
        eid, body = next(iter(entry.items()))
        if isinstance(body, dict):
            out.append((eid, body))
    return out


def as_envelope(elements):
    if isinstance(elements, dict) and "elements" in elements:
        return elements
    if isinstance(elements, list) and elements and isinstance(elements[0], dict):
        return {"elements": elements}
    raise ValueError("elements must be an envelope, a list of {id: body} entries")


def _num(v):
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


def box_of(body):
    if not isinstance(body, dict):
        return None
    x, y = _num(body.get("x")), _num(body.get("y"))
    w, h = _num(body.get("width")), _num(body.get("height"))
    if None in (x, y) or w is None or h is None:
        return None
    if w <= 0 or h <= 0:
        return None
    return (x, y, w, h)


def center_of(body):
    b = box_of(body)
    if b is None:
        return None
    return (b[0] + b[2] / 2.0, b[1] + b[3] / 2.0)


def box_area(b):
    return max(0.0, b[2]) * max(0.0, b[3])


def box_gap(a, b):
    dx = max(0.0, max(a[0] - (b[0] + b[2]), b[0] - (a[0] + a[2])))
    dy = max(0.0, max(a[1] - (b[1] + b[3]), b[1] - (a[1] + a[3])))
    return math.hypot(dx, dy)


def point_box_distance(p, b):
    px, py = p
    dx = max(b[0] - px, 0.0, px - (b[0] + b[2]))
    dy = max(b[1] - py, 0.0, py - (b[1] + b[3]))
    return math.hypot(dx, dy)


def iou(a, b):
    if not a or not b:
        return 0.0
    x1, y1 = max(a[0], b[0]), max(a[1], b[1])
    x2, y2 = min(a[0] + a[2], b[0] + b[2]), min(a[1] + a[3], b[1] + b[3])
    inter = max(0.0, x2 - x1) * max(0.0, y2 - y1)
    if inter <= 0:
        return 0.0
    union = box_area(a) + box_area(b) - inter
    return inter / union if union > 0 else 0.0


def union_boxes(boxes):
    boxes = [b for b in boxes if b]
    if not boxes:
        return None
    x1 = min(b[0] for b in boxes)
    y1 = min(b[1] for b in boxes)
    x2 = max(b[0] + b[2] for b in boxes)
    y2 = max(b[1] + b[3] for b in boxes)
    return (x1, y1, x2 - x1, y2 - y1)


def expand_box(box, margin_ratio=0.15, min_margin=12.0, width=None, height=None):
    if box is None:
        return None
    mx = max(min_margin, box[2] * margin_ratio)
    my = max(min_margin, box[3] * margin_ratio)
    x, y = box[0] - mx, box[1] - my
    w, h = box[2] + 2 * mx, box[3] + 2 * my
    if width is not None:
        x = max(0.0, x)
        w = min(w, width - x)
    if height is not None:
        y = max(0.0, y)
        h = min(h, height - y)
    return (x, y, w, h)


def anchor_point(box, toward):
    if box is None or toward is None:
        return None
    cx, cy = box[0] + box[2] / 2.0, box[1] + box[3] / 2.0
    dx, dy = toward[0] - cx, toward[1] - cy
    if dx == 0 and dy == 0:
        return (cx, cy)
    hw, hh = box[2] / 2.0, box[3] / 2.0
    scale = min(hw / abs(dx) if dx else float("inf"),
                hh / abs(dy) if dy else float("inf"))
    return (cx + dx * scale, cy + dy * scale)


def flow_endpoints(entry_id, body, by_id):
    s = body.get("start") or body.get("startPoint")
    e = body.get("end") or body.get("endPoint")

    def _pt(d):
        if isinstance(d, dict):
            x, y = _num(d.get("x")), _num(d.get("y"))
            if None not in (x, y):
                return (x, y)
        if isinstance(d, (list, tuple)) and len(d) >= 2:
            x, y = _num(d[0]), _num(d[1])
            if None not in (x, y):
                return (x, y)
        return None

    p_s, p_e = _pt(s), _pt(e)
    if p_s is None:
        src = by_id.get(body.get("sourceRef"))
        p_s = center_of(src) if src else None
    if p_e is None:
        tgt = by_id.get(body.get("targetRef"))
        p_e = center_of(tgt) if tgt else None
    return p_s, p_e


def node_degrees(envelope):
    items = iter_elements(envelope)
    deg = {}
    for eid, body in items:
        if body.get("class") in FLOW_NODES:
            deg[eid] = {"in": [], "out": []}
    for eid, body in items:
        if body.get("class") != "SequenceFlow":
            continue
        s, t = body.get("sourceRef"), body.get("targetRef")
        if s in deg:
            deg[s]["out"].append(eid)
        if t in deg:
            deg[t]["in"].append(eid)
    return deg


def dangling_nodes(envelope):
    items = dict(iter_elements(envelope))
    deg = node_degrees(envelope)
    out = {}
    for eid, body in items.items():
        cls = body.get("class")
        if cls not in FLOW_NODES:
            continue
        sub = (body.get("subType") or "").strip()
        d = deg.get(eid, {"in": [], "out": []})
        missing = []
        if cls in ("Activity", "Gateway"):
            if not d["in"]:
                missing.append("incoming")
            if not d["out"]:
                missing.append("outgoing")
        elif cls == "Event":
            if sub == "StartEvent" and d["in"]:
                missing.append("start_has_incoming")
            elif sub == "EndEvent" and d["out"]:
                missing.append("end_has_outgoing")
            elif sub not in ("StartEvent", "EndEvent"):
                if not d["in"]:
                    missing.append("incoming")
                if not d["out"]:
                    missing.append("outgoing")
        if missing:
            out[eid] = {
                "class": cls,
                "name": body.get("name") or "",
                "missing": "both" if len(missing) == 2 else missing[0],
                "detail": missing,
                "box": box_of(body),
            }
    return out


def orphan_endpoints(envelope, tol=18.0):
    items = iter_elements(envelope)
    by_id = dict(items)
    boxes = [(eid, box_of(b)) for eid, b in items
             if b.get("class") in SHAPES and box_of(b)]
    out = []
    for eid, body in items:
        if body.get("class") not in CONNECTIONS:
            continue
        for role, p in zip(("start", "end"), flow_endpoints(eid, body, by_id)):
            if p is None or not boxes:
                continue
            best = min(boxes, key=lambda kv: point_box_distance(p, kv[1]))
            dist = point_box_distance(p, best[1])
            if dist > tol:
                out.append({"flow": eid, "role": role, "point": p,
                            "nearest": best[0], "distance": round(dist, 1)})
    return out


def boxes_near(envelope, point, radius):
    out = []
    for eid, body in iter_elements(envelope):
        if body.get("class") not in SHAPES:
            continue
        c = center_of(body)
        if c is None:
            continue
        d = math.hypot(c[0] - point[0], c[1] - point[1])
        if d <= radius:
            out.append((eid, body, d))
    out.sort(key=lambda t: t[2])
    return out


def self_test():
    b = (0, 0, 100, 50)
    assert box_of({"x": 0, "y": 0, "width": 100, "height": 50}) == b
    assert box_of({"x": 0, "y": 0}) is None
    assert abs(box_gap(b, (150, 0, 50, 50)) - 50) < 1e-6
    assert box_gap(b, (10, 10, 10, 10)) == 0
    assert point_box_distance((0, 0), b) == 0
    assert abs(point_box_distance((-10, 25), b) - 10) < 1e-6
    assert iou(b, b) == 1.0
    assert iou(b, (200, 0, 10, 10)) == 0.0
    assert union_boxes([b, (200, 100, 10, 10)]) == (0, 0, 210, 110)
    p = anchor_point(b, (200, 25))
    assert abs(p[0] - 100) < 1e-6 and abs(p[1] - 25) < 1e-6
    env = {"elements": [
        {"A": {"class": "Activity", "x": 0, "y": 0, "width": 100, "height": 50}},
        {"B": {"class": "Activity", "x": 200, "y": 0, "width": 100, "height": 50}},
        {"F": {"class": "SequenceFlow", "sourceRef": "A", "targetRef": "B"}},
    ]}
    assert node_degrees(env)["A"]["out"] == ["F"]
    assert dangling_nodes(env)["A"]["missing"] == "incoming"
    assert dangling_nodes(env)["B"]["missing"] == "outgoing"
    body = {"class": "Association", "sourceRef": "A", "targetRef": "B",
            "start": {"x": 0, "y": 0}, "end": {"x": 999, "y": 999}}
    assert flow_endpoints("X", body, {"A": {"x": 0, "y": 0, "width": 1, "height": 1}}) == ((0.0, 0.0), (999.0, 999.0))
    print("bpmn_geometry self-test OK")


if __name__ == "__main__":
    self_test()
