from __future__ import annotations

import argparse
import csv
import json
import os
import re
import sys
import xml.etree.ElementTree as ET
from collections import Counter, OrderedDict

BPMN = "http://www.omg.org/spec/BPMN/20100524/MODEL"
BPMNDI = "http://www.omg.org/spec/BPMN/20100524/DI"
DC = "http://www.omg.org/spec/DD/20100524/DC"
DIX = "http://www.omg.org/spec/DD/20100524/DI"
XSI = "http://www.w3.org/2001/XMLSchema-instance"

for _p, _u in (("", BPMN), ("bpmndi", BPMNDI), ("omgdc", DC), ("omgdi", DIX), ("xsi", XSI)):
    try:
        ET.register_namespace(_p, _u)
    except ValueError:
        pass


def q(ns, local):
    return "{%s}%s" % (ns, local)


def local_name(tag):
    return tag.split("}", 1)[1] if isinstance(tag, str) and "}" in tag else tag


ACTIVITY_TAGS = {
    "Task": "task", "UserTask": "userTask", "ServiceTask": "serviceTask",
    "ManualTask": "manualTask", "ScriptTask": "scriptTask", "SendTask": "sendTask",
    "ReceiveTask": "receiveTask", "CallActivity": "callActivity", "SubProcess": "subProcess",
}
GATEWAY_TAGS = {
    "Exclusive": "exclusiveGateway", "Parallel": "parallelGateway",
    "Inclusive": "inclusiveGateway", "EventBased": "eventBasedGateway",
    "Complex": "complexGateway",
}
EVENT_TAGS = {
    "StartEvent": "startEvent", "EndEvent": "endEvent",
    "IntermediateCatchEvent": "intermediateCatchEvent",
    "IntermediateThrowEvent": "intermediateThrowEvent",
    "IntermediateSendEvent": "intermediateThrowEvent",
    "BoundaryEvent": "boundaryEvent",
}
EVENT_DEF_TAGS = {
    "Message": "messageEventDefinition", "Timer": "timerEventDefinition",
    "Error": "errorEventDefinition", "Signal": "signalEventDefinition",
    "Terminate": "terminateEventDefinition", "Escalation": "escalationEventDefinition",
}

FLOW_NODE_TAGS = set(ACTIVITY_TAGS.values()) | set(GATEWAY_TAGS.values()) | set(EVENT_TAGS.values())
DATA_REF_TAGS = {"dataObjectReference", "dataStoreReference"}
CONNECTION_TAGS = {"sequenceFlow", "messageFlow", "association"}

TAG_TO_CLASS = {}
TAG_TO_CLASS.update({v: "Activity" for v in ACTIVITY_TAGS.values()})
TAG_TO_CLASS.update({v: "Gateway" for v in GATEWAY_TAGS.values()})
TAG_TO_CLASS.update({v: "Event" for v in EVENT_TAGS.values()})
TAG_TO_CLASS.update({"participant": "Pool", "lane": "Lane"})
TAG_TO_CLASS.update({"dataObject": "DataObject", "dataObjectReference": "DataObject",
                     "dataStoreReference": "DataObject"})
TAG_TO_CLASS.update({v: "SequenceFlow" for v in ("sequenceFlow",)})
TAG_TO_CLASS.update({"messageFlow": "MessageFlow", "association": "Association",
                     "dataInputAssociation": "Association",
                     "dataOutputAssociation": "Association"})


_FENCE_RE = re.compile(r"^\s*```(?:xml|json)?\s*|\s*```\s*$", re.IGNORECASE)


def _strip_fences(text):
    if not isinstance(text, str):
        return ""
    t = text.strip()
    if t.startswith("```"):
        t = re.sub(r"^\s*```[a-zA-Z]*\s*", "", t)
        t = re.sub(r"\s*```\s*$", "", t)
    return t.strip()


def _strip_to_root(xml_text):
    t = _strip_fences(xml_text)
    m = re.search(r"<(?:\?xml|definitions)", t)
    if m and m.start() > 0:
        t = t[m.start():]
    m = re.search(r"</definitions>\s*$", t)
    if m:
        t = t[:m.end()]
    return t


def _repair_loose_json(text):
    t = _strip_fences(text)
    t = re.sub(r'([{,]\s*)([A-Za-z_][A-Za-z0-9_]*)\s*:', r'\1"\2":', t)
    t = re.sub(r",\s*([}\]])", r"\1", t)
    return t


def load_elements(elements):
    data = elements
    if isinstance(elements, str):
        t = _strip_fences(elements)
        try:
            data = json.loads(t)
        except Exception:
            data = json.loads(_repair_loose_json(t))
    if isinstance(data, dict):
        data = data.get("elements", data)
    if isinstance(data, dict):
        data = [data]
    out = []
    for entry in data:
        if not isinstance(entry, dict):
            continue
        if len(entry) == 1 and "class" not in entry:
            oid, body = next(iter(entry.items()))
        else:
            oid, body = entry.get("id"), entry
        if not isinstance(body, dict):
            continue
        body = dict(body)
        for a, b in (("incoming", "sourceRef"), ("outgoing", "targetRef")):
            val = body.get(a)
            if isinstance(val, list):
                val = val[0] if len(val) == 1 else (val[0] if val else None)
            if val and not body.get(b):
                body[b] = val
        if not oid:
            oid = body.get("id") or "Object%d" % (len(out) + 1)
        out.append((str(oid), body))
    return out


class SyncReport(object):
    def __init__(self):
        self.parsed = True
        self.rebuilt = False
        self.created_collaboration = 0
        self.created_process = 0
        self.added = []
        self.renamed = []
        self.id_map = {}
        self.retagged = []
        self.normalized_data = []
        self.fixed_refs = []
        self.lane_refs_added = 0
        self.added_shapes = []
        self.added_edges = []
        self.fixed_bounds = []
        self.removed_di = []
        self.extra_xml = []
        self.unmatched_json = []
        self.notes = []

    def as_dict(self):
        return OrderedDict([
            ("parsed", self.parsed),
            ("rebuilt_from_scratch", self.rebuilt),
            ("created_collaboration", self.created_collaboration),
            ("created_process", self.created_process),
            ("added_semantic", self.added),
            ("renamed_to_json_id", self.renamed),
            ("retagged", self.retagged),
            ("normalized_data_object", self.normalized_data),
            ("fixed_refs", self.fixed_refs),
            ("lane_flow_node_refs_added", self.lane_refs_added),
            ("added_shapes", self.added_shapes),
            ("added_edges", self.added_edges),
            ("fixed_bounds", self.fixed_bounds),
            ("removed_orphan_di", self.removed_di),
            ("extra_xml_only", self.extra_xml),
            ("unmatched_json", self.unmatched_json),
            ("notes", self.notes),
            ("counts", {
                "added_semantic": len(self.added),
                "renamed": len(self.renamed),
                "added_shapes": len(self.added_shapes),
                "added_edges": len(self.added_edges),
                "normalized_data_object": len(self.normalized_data),
                "removed_orphan_di": len(self.removed_di),
                "fixed_refs": len(self.fixed_refs),
            }),
        ])

    def is_clean(self):
        return not any([self.added, self.renamed, self.retagged, self.normalized_data,
                        self.fixed_refs, self.added_shapes, self.added_edges,
                        self.fixed_bounds, self.removed_di, self.lane_refs_added])

    def __str__(self):
        c = self.as_dict()["counts"]
        return ("[bpmn_sync] +semantic=%d +retag=%d +dataref=%d +ref=%d +shape=%d +edge=%d "
                "-orphanDI=%d rebuilt=%s" % (
                    c["added_semantic"], len(self.retagged), c["normalized_data_object"],
                    c["fixed_refs"], c["added_shapes"], c["added_edges"],
                    c["removed_orphan_di"], self.rebuilt))


def _norm_text(s):
    return re.sub(r"\s+", " ", (s or "")).strip().lower()


def _int(v, default=None):
    try:
        if isinstance(v, bool):
            return default
        return int(round(float(v)))
    except Exception:
        return default


def _is_flow_node(elem):
    return local_name(elem.tag) in FLOW_NODE_TAGS


def _parent_map(root):
    return {c: p for p in root.iter() for c in p}


REF_ATTRS = {"sourceRef", "targetRef", "dataObjectRef", "processRef", "bpmnElement",
             "attachedToRef", "default"}
REF_CHILD_TAGS = {"flowNodeRef", "sourceRef", "targetRef", "incoming", "outgoing"}


def _referenced_ids(root):
    refs = set()
    for e in root.iter():
        for k, v in e.attrib.items():
            if local_name(k) in REF_ATTRS and v:
                refs.add(v.strip())
        if local_name(e.tag) in REF_CHILD_TAGS and (e.text or "").strip():
            refs.add(e.text.strip())
    return refs


def _conn_refs(el):
    src = el.get("sourceRef")
    tgt = el.get("targetRef")
    for c in el:
        t = local_name(c.tag)
        if t == "sourceRef" and not src:
            src = (c.text or "").strip() or src
        elif t == "targetRef" and not tgt:
            tgt = (c.text or "").strip() or tgt
    return (src, tgt)


def _iter_local(root, name):
    for e in root.iter():
        if local_name(e.tag) == name:
            yield e


def _di_tag_map(name):
    if name in ("BPMNDiagram", "BPMNPlane", "BPMNShape", "BPMNEdge", "BPMNLabel"):
        return BPMNDI
    if name == "Bounds":
        return DC
    if name == "waypoint":
        return DIX
    return None


def _canonicalize_di(elem):
    def rec(e):
        ns = _di_tag_map(local_name(e.tag))
        if ns:
            e.tag = q(ns, local_name(e.tag))
        for c in e:
            rec(c)
    rec(elem)
    return elem


def _normalize_data_association(root, report):
    n = 0
    for tag in ("dataInputAssociation", "dataOutputAssociation"):
        for e in root.iter(q(BPMN, tag)):
            for child in [c for c in list(e) if local_name(c.tag) == "targetRef"]:
                val = (child.text or "").strip()
                if val and not e.get("targetRef"):
                    e.set("targetRef", val)
                e.remove(child)
                n += 1
            attr_src = e.get("sourceRef")
            if attr_src:
                ch = ET.SubElement(e, q(BPMN, "sourceRef"))
                ch.text = attr_src
                e.attrib.pop("sourceRef", None)
                n += 1
    if n:
        report.notes.append("修正 data association 引用写法 %d 处" % n)
    for tag in ("sequenceFlow", "messageFlow", "association"):
        for e in root.iter(q(BPMN, tag)):
            for child in [c for c in list(e) if local_name(c.tag) in ("sourceRef", "targetRef")]:
                val = (child.text or "").strip()
                f = local_name(child.tag)
                if val and not e.get(f):
                    e.set(f, val)
                e.remove(child)
                n += 1
    return n


def _new_definitions():
    root = ET.Element(q(BPMN, "definitions"))
    root.set("targetNamespace", "http://www.signavio.com/bpmn20")
    root.set("xmlns:bpmndi", BPMNDI)
    root.set("xmlns:omgdc", DC)
    root.set("xmlns:omgdi", DIX)
    root.set("xmlns:xsi", XSI)
    root.set(q(XSI, "schemaLocation"),
             BPMN + " http://www.omg.org/spec/BPMN/2.0/20100501/BPMN20.xsd")
    return root


def parse_bpmn(xml_text):
    t = _strip_to_root(xml_text)
    if not t:
        return None
    try:
        return ET.fromstring(t)
    except ET.ParseError:
        return None


def _find_or_create(parent, tag):
    el = parent.find(q(BPMN, tag)) if parent is not None else None
    if el is None and parent is not None:
        el = ET.SubElement(parent, q(BPMN, tag))
    return el


def _ensure_unique_id(root, wanted, taken):
    if wanted not in taken:
        taken.add(wanted)
        return wanted
    i = 2
    while "%s_%d" % (wanted, i) in taken:
        i += 1
    nid = "%s_%d" % (wanted, i)
    taken.add(nid)
    return nid


def _coords(body):
    x, y = _int(body.get("x")), _int(body.get("y"))
    w, h = _int(body.get("width")), _int(body.get("height"))
    if None in (x, y, w, h):
        return None
    return x, y, max(1, w), max(1, h)


def _point(body, key):
    p = body.get(key)
    if not isinstance(p, dict):
        return None
    x, y = _int(p.get("x")), _int(p.get("y"))
    if x is None or y is None:
        return None
    return x, y


def synchronize(elements, bpmn_xml=None, normalize_data_object=True, add_di=True,
                add_semantic=True):
    report = SyncReport()
    items = load_elements(elements)
    if not items:
        report.notes.append("JSON 为空，未做任何修改")
        return (bpmn_xml or ""), report

    root = parse_bpmn(bpmn_xml) if bpmn_xml else None
    if root is None:
        root = _new_definitions()
        report.rebuilt = True
        report.parsed = bool(bpmn_xml)
    if local_name(root.tag) != "definitions":
        new_root = _new_definitions()
        for k, v in root.attrib.items():
            new_root.set(k, v)
        for c in list(root):
            new_root.append(c)
        root = new_root
        report.rebuilt = True

    parent = _parent_map(root)
    by_id = {}
    for e in root.iter():
        eid = e.get("id")
        if eid and eid not in by_id:
            by_id[eid] = e
    taken = set(by_id)

    pools = [(oid, b) for oid, b in items if b.get("class") == "Pool"]
    lanes = [(oid, b) for oid, b in items if b.get("class") == "Lane"]
    single_pool_id = pools[0][0] if len(pools) == 1 else None

    pool_of = {}
    for oid, b in pools:
        pool_of[oid] = oid
    for oid, b in lanes:
        pool_of[oid] = b.get("parent") if b.get("parent") in dict(pools) else single_pool_id
    for oid, b in items:
        if oid in pool_of:
            continue
        owner = None
        for loid, lb in lanes:
            cont = lb.get("contains") or []
            if oid in cont:
                owner = pool_of.get(loid)
                break
        if owner is None:
            for poid, pb in pools:
                if oid in (pb.get("contains") or []):
                    owner = poid
                    break
        pool_of[oid] = owner or single_pool_id

    collaboration = root.find(q(BPMN, "collaboration"))
    processes = [p for p in root.findall(q(BPMN, "process"))]
    if pools or any(b.get("class") == "MessageFlow" for _, b in items):
        if collaboration is None:
            collaboration = ET.Element(q(BPMN, "collaboration"))
            collaboration.set("id", _ensure_unique_id(root, "Collaboration_1", taken))
            root.insert(0, collaboration)
            report.created_collaboration = 1

    def ensure_process_for(pool_id):
        participant = None
        if pool_id:
            participant = by_id.get(pool_id)
            if participant is None or local_name(participant.tag) != "participant":
                participant = None
        if participant is not None:
            ref = participant.get("processRef")
            if ref and ref in by_id and local_name(by_id[ref].tag) == "process":
                return by_id[ref]
        claimed = set()
        if collaboration is not None:
            for p in collaboration.findall(q(BPMN, "participant")):
                if p.get("processRef"):
                    claimed.add(p.get("processRef"))
        free = [p for p in processes if p.get("id") not in claimed]
        if free:
            proc = free[0]
        else:
            proc = ET.SubElement(root, q(BPMN, "process"))
            proc.set("id", _ensure_unique_id(root, "Process_%d" % (len(processes) + 1), taken))
            proc.set("isExecutable", "false")
            processes.append(proc)
            report.created_process += 1
            by_id[proc.get("id")] = proc
            parent[proc] = root
        if participant is not None and not participant.get("processRef"):
            participant.set("processRef", proc.get("id"))
        return proc

    if not processes:
        proc = ET.SubElement(root, q(BPMN, "process"))
        proc.set("id", _ensure_unique_id(root, "Process_1", taken))
        proc.set("isExecutable", "false")
        processes.append(proc)
        report.created_process += 1
        by_id[proc.get("id")] = proc
        parent[proc] = root

    default_proc = processes[0]
    proc_for_pool = {}
    for poid, _b in pools:
        proc_for_pool[poid] = ensure_process_for(poid)

    def container_for(oid, body):
        cls = body.get("class")
        if cls == "Pool":
            return collaboration if collaboration is not None else default_proc
        if cls == "MessageFlow":
            return collaboration if collaboration is not None else default_proc
        if cls == "Lane":
            proc = proc_for_pool.get(pool_of.get(oid))
            if proc is None:
                proc = default_proc
            return _lane_set_of(proc)
        proc = proc_for_pool.get(pool_of.get(oid))
        if proc is None:
            proc = default_proc
        return proc

    def _lane_set_of(proc):
        ls = proc.find(q(BPMN, "laneSet"))
        if ls is None:
            ls = ET.Element(q(BPMN, "laneSet"))
            ls.set("id", _ensure_unique_id(root, "LaneSet_%s" % (proc.get("id") or "1"), taken))
            proc.insert(0, ls)
            by_id[ls.get("id")] = ls
            parent[ls] = proc
        return ls

    json_ids = [oid for oid, _ in items]
    matched = {}
    used_xml_ids = set()
    for oid, body in items:
        if body.get("class") in ("Pool", "Lane"):
            want = {"Pool": "participant", "Lane": "lane"}[body["class"]]
            el = by_id.get(oid)
            if el is not None and local_name(el.tag) == want:
                matched[oid] = el
                used_xml_ids.add(oid)
            continue
        el = by_id.get(oid)
        if el is None:
            continue
        cls = body.get("class")
        if cls == "DataObject":
            if local_name(el.tag) in ("dataObject", "dataObjectReference", "dataStoreReference"):
                matched[oid] = el
                used_xml_ids.add(oid)
        elif TAG_TO_CLASS.get(local_name(el.tag)) == cls:
            matched[oid] = el
            used_xml_ids.add(oid)
        elif cls in ("SequenceFlow", "MessageFlow", "Association") and \
                TAG_TO_CLASS.get(local_name(el.tag)) in ("SequenceFlow", "MessageFlow", "Association"):
            matched[oid] = el
            used_xml_ids.add(oid)

    for oid, body in items:
        if oid in matched:
            continue
        cls = body.get("class")
        if cls in ("SequenceFlow", "MessageFlow", "Association", "Pool", "Lane"):
            continue
        name = _norm_text(body.get("name"))
        cands = [e for e in root.iter()
                 if e.get("id") not in used_xml_ids and TAG_TO_CLASS.get(local_name(e.tag)) == cls]
        if name:
            hit = [e for e in cands if _norm_text(e.get("name")) == name]
            if len(hit) == 1:
                matched[oid] = hit[0]
                used_xml_ids.add(hit[0].get("id"))

    for oid, body in items:
        if oid in matched:
            continue
        cls = body.get("class")
        coords = _coords(body)
        if cls in ("SequenceFlow", "MessageFlow", "Association", "Pool", "Lane") or not coords:
            continue
        cx, cy = coords[0] + coords[2] / 2.0, coords[1] + coords[3] / 2.0
        best, bestd = None, None
        for eid, xx, yy in _shape_centers(root):
            if eid in used_xml_ids:
                continue
            el = by_id.get(eid)
            if el is None or TAG_TO_CLASS.get(local_name(el.tag)) != cls:
                continue
            d = ((xx - cx) ** 2 + (yy - cy) ** 2) ** 0.5
            if bestd is None or d < bestd:
                best, bestd = el, d
        if best is not None and bestd is not None and bestd <= 80:
            matched[oid] = best
            used_xml_ids.add(best.get("id"))

    xml_of = {oid: el.get("id") for oid, el in matched.items()}
    for oid, body in items:
        if oid in matched:
            continue
        cls = body.get("class")
        if cls not in ("SequenceFlow", "MessageFlow", "Association"):
            continue
        s = xml_of.get(body.get("sourceRef"), body.get("sourceRef"))
        t = xml_of.get(body.get("targetRef"), body.get("targetRef"))
        cands = [e for e in root.iter()
                 if local_name(e.tag) in CONNECTION_TAGS and e.get("id") not in used_xml_ids]
        hit = [e for e in cands
               if _conn_refs(e) == (s, t)
               and TAG_TO_CLASS.get(local_name(e.tag)) == cls]
        if len(hit) == 1:
            matched[oid] = hit[0]
            used_xml_ids.add(hit[0].get("id"))

    refs = _referenced_ids(root)
    for oid, el in list(matched.items()):
        cur = el.get("id")
        if not cur or cur == oid or oid in by_id or cur in refs:
            continue
        by_id.pop(cur, None)
        el.set("id", oid)
        by_id[oid] = el
        taken.add(oid)
        used_xml_ids.discard(cur)
        used_xml_ids.add(oid)
        report.renamed.append({"from": cur, "to": oid})
    if report.renamed:
        alias = {r["from"]: r["to"] for r in report.renamed}
        for s in root.iter():
            bel = s.get("bpmnElement")
            if bel in alias:
                s.set("bpmnElement", alias[bel])

    id_map = {oid: (el.get("id") or oid) for oid, el in matched.items()}
    report.id_map = dict(id_map)

    def resolve_ref(ref):
        return id_map.get(ref, ref)

    def make_element(oid, body):
        cls = body["class"]
        name = body.get("name") or ""
        if cls == "Pool":
            tag = "participant"
        elif cls == "Lane":
            tag = "lane"
        elif cls == "Activity":
            tag = ACTIVITY_TAGS.get(body.get("activityType") or "Task", "task")
        elif cls == "Gateway":
            tag = GATEWAY_TAGS.get(body.get("gatewayType") or "Exclusive", "exclusiveGateway")
        elif cls == "Event":
            tag = EVENT_TAGS.get(body.get("subType") or "StartEvent", "startEvent")
        elif cls == "DataObject":
            tag = "dataStoreReference" if body.get("dataType") == "DataStore" else "dataObjectReference"
        elif cls in ("SequenceFlow", "MessageFlow", "Association"):
            tag = {"SequenceFlow": "sequenceFlow", "MessageFlow": "messageFlow",
                   "Association": "association"}[cls]
        else:
            return None
        el = ET.Element(q(BPMN, tag))
        el.set("id", _ensure_unique_id(root, oid, taken))
        if name:
            el.set("name", name)
        if cls == "Event":
            et = body.get("eventType")
            if et in EVENT_DEF_TAGS:
                d = ET.SubElement(el, q(BPMN, EVENT_DEF_TAGS[et]))
                d.set("id", _ensure_unique_id(root, "%s_%sDef" % (el.get("id"), et), taken))
        if cls == "DataObject" and tag == "dataObjectReference":
            do = ET.Element(q(BPMN, "dataObject"))
            do.set("id", _ensure_unique_id(root, "%s_do" % el.get("id"), taken))
            el.set("dataObjectRef", do.get("id"))
            container_for(oid, body).append(do)
            by_id[do.get("id")] = do
        if cls in ("SequenceFlow", "MessageFlow", "Association"):
            el.set("sourceRef", resolve_ref(body.get("sourceRef")) or "")
            el.set("targetRef", resolve_ref(body.get("targetRef")) or "")
            if cls == "Association" and body.get("arrow"):
                el.set("associationDirection", "One")
            if cls == "SequenceFlow" and not name and body.get("condition"):
                el.set("name", str(body["condition"]))
        if cls == "Pool":
            proc = proc_for_pool.get(oid)
            if proc is not None:
                el.set("processRef", proc.get("id"))
        return el

    order = sorted(items, key=lambda kv: {"Pool": 0, "Lane": 1}.get(kv[1].get("class"), 2))
    for oid, body in order:
        if body.get("class") in (None, "Unknown"):
            report.unmatched_json.append(oid)
            continue
        el = matched.get(oid)
        if el is None:
            if not add_semantic:
                report.unmatched_json.append(oid)
                continue
            el = make_element(oid, body)
            if el is None:
                report.unmatched_json.append(oid)
                continue
            container = container_for(oid, body)
            if container is None:
                container = default_proc
            container.append(el)
            matched[oid] = el
            by_id[el.get("id")] = el
            parent[el] = container
            used_xml_ids.add(el.get("id"))
            id_map[oid] = el.get("id")
            report.added.append({"id": oid, "class": body["class"],
                                 "tag": local_name(el.tag), "reason": "JSON 有、XML 无"})
            continue

        cls = body.get("class")
        cur = local_name(el.tag)
        want = None
        if cls == "DataObject":
            want = "dataStoreReference" if body.get("dataType") == "DataStore" else "dataObjectReference"
            if cur == "dataObject":
                if not normalize_data_object:
                    want = None
            elif cur in DATA_REF_TAGS and want is not None:
                if cur == "dataObjectReference" and want == "dataObjectReference":
                    want = None
                elif cur == "dataStoreReference" and want == "dataStoreReference":
                    want = None
        elif cls == "Activity":
            want = ACTIVITY_TAGS.get(body.get("activityType") or "Task", "task")
            want = None if want == cur else want
        elif cls == "Gateway":
            want = GATEWAY_TAGS.get(body.get("gatewayType") or "Exclusive", "exclusiveGateway")
            want = None if want == cur else want
        elif cls == "Event":
            want = EVENT_TAGS.get(body.get("subType") or "StartEvent", "startEvent")
            want = None if want == cur else want
        elif cls in ("SequenceFlow", "MessageFlow", "Association"):
            if cur in ("dataInputAssociation", "dataOutputAssociation"):
                want = None
            else:
                want = {"SequenceFlow": "sequenceFlow", "MessageFlow": "messageFlow",
                        "Association": "association"}[cls]
                want = None if want == cur else want

        if want and want != cur:
            cont = parent.get(el)
            el.tag = q(BPMN, want)
            if cls == "DataObject" and cur == "dataObject" and want in DATA_REF_TAGS:
                do_id = _ensure_unique_id(root, "%s_do" % el.get("id"), taken)
                if want == "dataObjectReference":
                    el.set("dataObjectRef", do_id)
                    host = cont if cont is not None else default_proc
                    do = ET.SubElement(host, q(BPMN, "dataObject"))
                    do.set("id", do_id)
                    by_id[do_id] = do
                if body.get("name"):
                    el.set("name", body["name"])
                report.normalized_data.append({"id": el.get("id"), "to": want})
            if cls in ("SequenceFlow", "MessageFlow", "Association"):
                right = container_for(oid, body)
                if right is not None and cont is not right:
                    if cont is not None:
                        cont.remove(el)
                    right.append(el)
                    parent[el] = right
            matched[oid] = el
            id_map[oid] = el.get("id")
            by_id[el.get("id")] = el
            report.retagged.append({"id": el.get("id"), "from": cur, "to": want})

        if cls in ("SequenceFlow", "MessageFlow", "Association"):
            is_data_assoc = local_name(el.tag) in ("dataInputAssociation", "dataOutputAssociation")
            if not is_data_assoc:
                for field in ("sourceRef", "targetRef"):
                    wantv = resolve_ref(body.get(field))
                    if wantv and wantv in by_id and el.get(field) != wantv:
                        report.fixed_refs.append({"id": el.get("id"), "field": field,
                                                  "old": el.get(field), "new": wantv})
                        el.set(field, wantv)
            else:
                want_t = resolve_ref(body.get("targetRef"))
                if want_t and want_t in by_id and el.get("targetRef") != want_t:
                    report.fixed_refs.append({"id": el.get("id"), "field": "targetRef",
                                              "old": el.get("targetRef"), "new": want_t})
                    el.set("targetRef", want_t)
                kids = {local_name(c.tag): c for c in el if local_name(c.tag) in ("sourceRef", "targetRef")}
                want_s = resolve_ref(body.get("sourceRef"))
                if want_s and want_s in by_id:
                    if "sourceRef" in kids:
                        if (kids["sourceRef"].text or "").strip() != want_s:
                            kids["sourceRef"].text = want_s
                            report.fixed_refs.append({"id": el.get("id"), "field": "sourceRef",
                                                      "old": None, "new": want_s})
                    else:
                        c = ET.SubElement(el, q(BPMN, "sourceRef"))
                        c.text = want_s
                        report.fixed_refs.append({"id": el.get("id"), "field": "sourceRef",
                                                  "old": None, "new": want_s})
            if cls == "Association" and body.get("arrow") and local_name(el.tag) == "association" \
                    and el.get("associationDirection") != "One":
                el.set("associationDirection", "One")
            if local_name(el.tag) in ("dataInputAssociation", "dataOutputAssociation"):
                el.attrib.pop("associationDirection", None)
            if cls == "SequenceFlow" and body.get("condition") and not el.get("name"):
                el.set("name", str(body["condition"]))
            cont = parent.get(el)
            right = collaboration if (cls == "MessageFlow" and collaboration is not None) else None
            if right is not None and cont is not right:
                cont.remove(el)
                right.append(el)
                parent[el] = right
            cont = parent.get(el)
            right = collaboration if (cls == "MessageFlow" and collaboration is not None) else None
            if right is not None and cont is not right:
                cont.remove(el)
                right.append(el)
                parent[el] = right

        if cls in ("Activity", "Event", "Gateway", "Lane", "Pool", "DataObject"):
            if body.get("name") and not (el.get("name") or "").strip():
                el.set("name", body["name"])
                report.notes.append("补 name: %s" % el.get("id"))

        if cls == "Event":
            et = body.get("eventType")
            if et in EVENT_DEF_TAGS and el.find(q(BPMN, EVENT_DEF_TAGS[et])) is None:
                d = ET.SubElement(el, q(BPMN, EVENT_DEF_TAGS[et]))
                d.set("id", _ensure_unique_id(root, "%s_%sDef" % (el.get("id"), et), taken))
                report.notes.append("补 eventDefinition: %s/%s" % (el.get("id"), et))

    for oid, body in lanes:
        el = matched.get(oid)
        if el is None:
            continue
        have = {r.text.strip() for r in el.findall(q(BPMN, "flowNodeRef")) if r.text}
        for child in body.get("contains") or []:
            cid = resolve_ref(child)
            target = by_id.get(cid)
            if target is None or not _is_flow_node(target):
                continue
            if cid not in have:
                ref = ET.SubElement(el, q(BPMN, "flowNodeRef"))
                ref.text = cid
                have.add(cid)
                report.lane_refs_added += 1

    _normalize_data_association(root, report)
    if add_di:
        _ensure_di(root, items, matched, by_id, parent, taken, report)

    xml_text = serialize(root)
    return xml_text, report


def _shape_centers(root):
    out = []
    for s in _iter_local(root, "BPMNShape"):
        b = s.find(q(DC, "Bounds"))
        if b is None:
            for c in s:
                if local_name(c.tag) == "Bounds":
                    b = c
                    break
        if b is None:
            continue
        x, y = _int(b.get("x")), _int(b.get("y"))
        w, h = _int(b.get("width"), 0), _int(b.get("height"), 0)
        if None in (x, y):
            continue
        out.append((s.get("bpmnElement"), x + w / 2.0, y + h / 2.0))
    return out


def _collect_diagrams(root, report):
    diagrams = list(_iter_local(root, "BPMNDiagram"))
    pmap = _parent_map(root)
    if not diagrams:
        diagram = ET.SubElement(root, q(BPMNDI, "BPMNDiagram"))
        diagram.set("id", "BPMNDiagram_1")
        report.notes.append("补建 BPMNDiagram")
        return ET.SubElement(diagram, q(BPMNDI, "BPMNPlane"))

    def score(d):
        ok = 1 if str(d.tag).startswith("{%s}" % BPMNDI) else 0
        n = sum(1 for _ in _iter_local(d, "BPMNShape")) + sum(1 for _ in _iter_local(d, "BPMNEdge"))
        return (ok, n)

    diagrams.sort(key=score, reverse=True)
    canonical = diagrams[0]
    _canonicalize_di(canonical)
    plane = None
    for c in canonical:
        if local_name(c.tag) == "BPMNPlane":
            plane = c
            break
    if plane is None:
        plane = ET.SubElement(canonical, q(BPMNDI, "BPMNPlane"))
        plane.set("id", _ensure_unique_id(root, "BPMNPlane_1", set(by_ids(root))))
    _canonicalize_di(plane)
    if not canonical.get("id"):
        canonical.set("id", _ensure_unique_id(root, "BPMNDiagram_1", set(by_ids(root))))
    if not plane.get("id"):
        plane.set("id", _ensure_unique_id(root, "BPMNPlane_1", set(by_ids(root))))

    existing = {s.get("bpmnElement") for s in plane if local_name(s.tag) in ("BPMNShape", "BPMNEdge")}
    for d in diagrams[1:]:
        for child in list(d):
            if local_name(child.tag) not in ("BPMNShape", "BPMNEdge"):
                continue
            _canonicalize_di(child)
            bel = child.get("bpmnElement")
            if bel in existing:
                report.removed_di.append(bel)
                continue
            d.remove(child)
            plane.append(child)
            existing.add(bel)
        holder = pmap.get(d)
        if holder is not None:
            holder.remove(d)
            report.notes.append("合并重复/命名空间错误的 BPMNDiagram: %s" % d.get("id"))
    report.notes.append("DI 命名空间已规范化为 bpmndi/omgdc/omgdi")
    return plane


def by_ids(root):
    for e in root.iter():
        if e.get("id"):
            yield e.get("id")


def _ensure_di(root, items, matched, by_id, parent, taken, report):
    plane = _collect_diagrams(root, report)
    coll = root.find(q(BPMN, "collaboration"))
    target = coll.get("id") if coll is not None else (
        root.find(q(BPMN, "process")).get("id") if root.find(q(BPMN, "process")) is not None else None)
    if target and plane.get("bpmnElement") != target:
        plane.set("bpmnElement", target)

    existing_shape = {}
    existing_edge = {}
    for s in list(plane):
        ln = local_name(s.tag)
        bel = s.get("bpmnElement")
        if ln == "BPMNShape":
            if bel in existing_shape:
                plane.remove(s)
                report.removed_di.append(bel)
                continue
            existing_shape[bel] = s
        elif ln == "BPMNEdge":
            if bel in existing_edge:
                plane.remove(s)
                report.removed_di.append(bel)
                continue
            existing_edge[bel] = s

    def alive(eid):
        el = by_id.get(eid)
        if el is None:
            return False
        t = local_name(el.tag)
        return t not in ("dataObject",)

    for bel, s in list(existing_shape.items()):
        if not alive(bel):
            plane.remove(s)
            del existing_shape[bel]
            report.removed_di.append(bel)
    for bel, s in list(existing_edge.items()):
        if not alive(bel):
            plane.remove(s)
            del existing_edge[bel]
            report.removed_di.append(bel)

    for oid, body in items:
        cls = body.get("class")
        if cls not in ("Pool", "Lane", "Activity", "Event", "Gateway", "DataObject"):
            continue
        el = matched.get(oid)
        if el is None:
            continue
        eid = el.get("id")
        if local_name(el.tag) == "dataObject":
            continue
        coords = _coords(body)
        shape = existing_shape.get(eid)
        if shape is None:
            shape = ET.SubElement(plane, q(BPMNDI, "BPMNShape"))
            shape.set("id", _ensure_unique_id(root, "%s_di" % eid, taken))
            shape.set("bpmnElement", eid)
            existing_shape[eid] = shape
            report.added_shapes.append(eid)
        if cls in ("Pool", "Lane"):
            shape.set("isHorizontal", "true")
        if cls == "Gateway" and body.get("gatewayType") in ("Exclusive", "Parallel", "Inclusive", "Complex"):
            shape.set("isMarkerVisible", "true")
        if coords:
            bounds = shape.find(q(DC, "Bounds"))
            if bounds is None:
                bounds = ET.SubElement(shape, q(DC, "Bounds"))
            want = {"x": str(coords[0]), "y": str(coords[1]),
                    "width": str(coords[2]), "height": str(coords[3])}
            if any(bounds.get(k) != v for k, v in want.items()):
                if bounds.get("x") is not None:
                    report.fixed_bounds.append(eid)
                for k, v in want.items():
                    bounds.set(k, v)

    for oid, body in items:
        if body.get("class") not in ("SequenceFlow", "MessageFlow", "Association"):
            continue
        el = matched.get(oid)
        if el is None:
            continue
        eid = el.get("id")
        start, end = _point(body, "start"), _point(body, "end")
        edge = existing_edge.get(eid)
        if edge is None:
            edge = ET.SubElement(plane, q(BPMNDI, "BPMNEdge"))
            edge.set("id", _ensure_unique_id(root, "%s_di" % eid, taken))
            edge.set("bpmnElement", eid)
            existing_edge[eid] = edge
            report.added_edges.append(eid)
        if not start and not end:
            continue
        wps = edge.findall(q(DIX, "waypoint"))
        mids = [(w.get("x"), w.get("y")) for w in wps[1:-1]] if len(wps) > 2 else []
        for w in wps:
            edge.remove(w)
        pts = []
        if start:
            pts.append(start)
        pts.extend(mids)
        if end:
            pts.append(end)
        if len(pts) < 2:
            pts = pts * 2 if len(pts) == 1 else pts
        for x, y in pts:
            w = ET.SubElement(edge, q(DIX, "waypoint"))
            w.set("x", str(x))
            w.set("y", str(y))


def serialize(root):
    try:
        ET.indent(root, space="  ")
    except AttributeError:
        pass
    body = ET.tostring(root, encoding="unicode")
    return '<?xml version="1.0" encoding="UTF-8"?>\n' + body


CLASS_TAGS = {
    "Pool": {"participant"},
    "Lane": {"lane"},
    "Event": set(EVENT_TAGS.values()),
    "Activity": set(ACTIVITY_TAGS.values()),
    "Gateway": set(GATEWAY_TAGS.values()),
    "DataObject": DATA_REF_TAGS,
    "SequenceFlow": {"sequenceFlow"},
    "MessageFlow": {"messageFlow"},
    "Association": {"association", "dataInputAssociation", "dataOutputAssociation"},
}


def conservation_check(elements, xml_text, alias=None):
    items = load_elements(elements)
    root = parse_bpmn(xml_text)
    out = OrderedDict()
    if root is None:
        return {"parse_error": True}
    alias = alias or {}
    refd = {e.get("dataObjectRef") for e in root.iter(q(BPMN, "dataObjectReference"))
            if e.get("dataObjectRef")}

    def set_of(cls):
        s = set()
        for e in root.iter():
            t = local_name(e.tag)
            if t in CLASS_TAGS.get(cls, ()):
                s.add(e.get("id"))
            elif cls == "DataObject" and t == "dataObject" and e.get("name") and e.get("id") not in refd:
                s.add(e.get("id"))
        return s

    for cls in ["Pool", "Lane", "Event", "Activity", "Gateway", "DataObject",
                "SequenceFlow", "MessageFlow", "Association"]:
        jids = [oid for oid, b in items if b.get("class") == cls]
        xids = set_of(cls)
        missing = [i for i in jids if alias.get(i, i) not in xids and i not in xids]
        out[cls] = {"json": len(jids), "xml": len(xids), "missing_ids": missing}
    return out


def di_coverage(xml_text):
    root = parse_bpmn(xml_text)
    if root is None:
        return {"error": "parse_failed"}
    shapes = {s.get("bpmnElement") for s in root.iter(q(BPMNDI, "BPMNShape"))}
    edges = {s.get("bpmnElement") for s in root.iter(q(BPMNDI, "BPMNEdge"))}
    n_shape_targets = 0
    n_edge_targets = 0
    miss = []
    for e in root.iter():
        t = local_name(e.tag)
        if t in FLOW_NODE_TAGS or t in DATA_REF_TAGS or t in ("participant", "lane"):
            n_shape_targets += 1
            if e.get("id") not in shapes:
                miss.append(e.get("id"))
        elif t in CONNECTION_TAGS:
            n_edge_targets += 1
            if e.get("id") not in edges:
                miss.append(e.get("id"))
    total = n_shape_targets + n_edge_targets
    return {"shapes": len(shapes), "edges": len(edges),
            "shape_targets": n_shape_targets, "edge_targets": n_edge_targets,
            "missing_di_ids": miss,
            "coverage": (1 - len(miss) / total) if total else 1.0}


def _final_json_for(sample_dir, base):
    import glob
    cands = [c for c in glob.glob(os.path.join(sample_dir, base + "_step2_round*_modified.json"))
             if "_invalid" not in c]

    def rnd(p):
        m = re.search(r"_round(\d+)_", p)
        return int(m.group(1)) if m else -1

    cands.sort(key=rnd)
    if cands:
        return cands[-1]
    p = os.path.join(sample_dir, base + "_step1_extraction_normalized.json")
    return p if os.path.exists(p) else None


def _bpmn_for(sample_dir, base):
    import glob
    for pat in (base + "_processed.bpmn", base + "_needs_revision_processed.bpmn",
                base + "*_processed.bpmn"):
        hits = sorted(glob.glob(os.path.join(sample_dir, pat)))
        if hits:
            return hits[0]
    return None


SKIP_DIRS = {"__pycache__", "image", "schemas", "标准文件", "HallucinationTest", ".venv"}


def repair_one(json_path, bpmn_path, out_path):
    items_raw = open(json_path, encoding="utf-8", errors="replace").read()
    xml_raw = open(bpmn_path, encoding="utf-8", errors="replace").read()
    xml_new, rep = synchronize(items_raw, xml_raw)
    if out_path:
        os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
        with open(out_path, "w", encoding="utf-8") as f:
            f.write(xml_new)
    return xml_new, rep


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--json")
    ap.add_argument("--bpmn")
    ap.add_argument("--out")
    ap.add_argument("--root")
    ap.add_argument("--report-dir", default="./sync_out")
    ap.add_argument("--self-test", action="store_true")
    ap.add_argument("--no-normalize-data", action="store_true")
    ap.add_argument("--no-semantic", action="store_true",
                    help="只修图元层/引用/类别，不补建缺失的语义元素")
    args = ap.parse_args(argv)

    if args.self_test:
        return _self_test()

    if args.json and args.bpmn:
        xml_new, rep = repair_one(args.json, args.bpmn, args.out)
        print(rep)
        print(json.dumps(rep.as_dict()["counts"], ensure_ascii=False))
        if not args.out:
            print(xml_new)
        return 0

    if not args.root:
        ap.print_help()
        return 2

    os.makedirs(args.report_dir, exist_ok=True)
    out_root = os.path.join(args.report_dir, "repaired")
    rows = []
    import glob
    for d in sorted(os.listdir(args.root)):
        full = os.path.join(args.root, d)
        if not os.path.isdir(full) or d.startswith(".") or d.startswith("backup") or d in SKIP_DIRS:
            continue
        bases = {os.path.basename(p).replace("_step1_extraction_normalized.json", "")
                 for p in glob.glob(os.path.join(full, "*_step1_extraction_normalized.json"))}
        for base in sorted(bases):
            jp, bp = _final_json_for(full, base), _bpmn_for(full, base)
            if not jp or not bp:
                continue
            out_dir = os.path.join(out_root, d)
            os.makedirs(out_dir, exist_ok=True)
            out_path = os.path.join(out_dir, os.path.basename(bp))
            try:
                items = open(jp, encoding="utf-8", errors="replace").read()
                before = open(bp, encoding="utf-8", errors="replace").read()
                before_di = di_coverage(before)
                xml_new, rep = synchronize(items, before,
                                           normalize_data_object=not args.no_normalize_data,
                                           add_semantic=not args.no_semantic)
                with open(out_path, "w", encoding="utf-8") as f:
                    f.write(xml_new)
                after_di = di_coverage(xml_new)
                cons_before = conservation_check(items, before)
                cons_after = conservation_check(items, xml_new, getattr(rep, "id_map", None))
                miss_b = {k: len(v["missing_ids"]) for k, v in cons_before.items() if isinstance(v, dict)}
                miss_a = {k: len(v["missing_ids"]) for k, v in cons_after.items() if isinstance(v, dict)}
                rows.append({
                    "variant": d, "sample": base,
                    "di_before": round(before_di.get("coverage", 0), 4),
                    "di_after": round(after_di.get("coverage", 0), 4),
                    "sem_missing_before": sum(miss_b.values()),
                    "sem_missing_after": sum(miss_a.values()),
                    "added_semantic": len(rep.added),
                    "added_shapes": len(rep.added_shapes),
                    "added_edges": len(rep.added_edges),
                    "normalized_data": len(rep.normalized_data),
                    "removed_orphan_di": len(rep.removed_di),
                    "rebuilt": rep.rebuilt,
                    "json": os.path.basename(jp), "bpmn": os.path.basename(bp),
                })
            except Exception as ex:
                rows.append({"variant": d, "sample": base, "error": repr(ex)[:160]})

    csv_path = os.path.join(args.report_dir, "sync_report.csv")
    keys = ["variant", "sample", "di_before", "di_after", "sem_missing_before",
            "sem_missing_after", "added_semantic", "added_shapes", "added_edges",
            "normalized_data", "removed_orphan_di", "rebuilt", "json", "bpmn", "error"]
    with open(csv_path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=keys)
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k, "") for k in keys})

    ok = [r for r in rows if "error" not in r]
    def avg(k):
        return sum(float(r[k]) for r in ok) / len(ok) if ok else 0.0
    lines = ["# 生成层确定性对齐报告", "",
             "- 处理样本：%d（失败 %d）" % (len(ok), len(rows) - len(ok)),
             "- DI 覆盖率：%.4f → %.4f" % (avg("di_before"), avg("di_after")),
             "- 语义层缺失元素总数：%d → %d" % (sum(int(r["sem_missing_before"]) for r in ok),
                                          sum(int(r["sem_missing_after"]) for r in ok)),
             "- 补建语义元素 %d 个 / 补 DI 图形 %d 个 / 补 DI 连线 %d 条 / 规范化 dataObject %d 个 / 清悬空 DI %d 个" % (
                 sum(int(r["added_semantic"]) for r in ok), sum(int(r["added_shapes"]) for r in ok),
                 sum(int(r["added_edges"]) for r in ok), sum(int(r["normalized_data"]) for r in ok),
                 sum(int(r["removed_orphan_di"]) for r in ok)),
             "", "## 修复前后 DI 覆盖率 < 1 的样本", "",
             "| 变体 | 样本 | DI 前 | DI 后 | 语义缺失 | 新增图元/连线 |",
             "|---|---|---|---|---|---|"]
    for r in sorted([r for r in ok if float(r["di_before"]) < 1.0], key=lambda x: float(x["di_before"]))[:40]:
        lines.append("| %s | %s | %.3f | %.3f | %s → %s | +%s/+%s |" % (
            r["variant"], r["sample"], float(r["di_before"]), float(r["di_after"]),
            r["sem_missing_before"], r["sem_missing_after"], r["added_shapes"], r["added_edges"]))
    with open(os.path.join(args.report_dir, "sync_summary.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    print("\n".join(lines[:8]))
    print("明细：" + csv_path)
    return 0


def _self_test():
    elems = {"elements": [
        {"Object1": {"class": "Pool", "name": "Insurer", "x": 10, "y": 10, "width": 800,
                     "height": 400, "contains": ["Object2"]}},
        {"Object2": {"class": "Lane", "name": "Officer", "x": 10, "y": 10, "width": 800,
                     "height": 400, "parent": "Object1", "contains": ["Object3", "Object4"]}},
        {"Object3": {"class": "Event", "name": "start", "subType": "StartEvent",
                     "eventType": "Message", "x": 40, "y": 60, "width": 36, "height": 36}},
        {"Object4": {"class": "Activity", "name": "Check", "activityType": "Task",
                     "x": 120, "y": 50, "width": 100, "height": 60}},
        {"Object5": {"class": "DataObject", "name": "claim", "dataType": "DataObject",
                     "x": 300, "y": 200, "width": 36, "height": 50}},
        {"Object6": {"class": "SequenceFlow", "name": "", "start": {"x": 76, "y": 78},
                     "end": {"x": 120, "y": 80}, "sourceRef": "Object3", "targetRef": "Object4"}},
        {"Object7": {"class": "Association", "arrow": True, "name": "",
                     "start": {"x": 336, "y": 200}, "end": {"x": 170, "y": 110},
                     "sourceRef": "Object5", "targetRef": "Object4"}},
    ]}
    broken = """<?xml version="1.0" encoding="UTF-8"?>
<definitions xmlns="http://www.omg.org/spec/BPMN/20100524/MODEL"
  xmlns:bpmndi="http://www.omg.org/spec/BPMN/20100524/DI"
  xmlns:omgdc="http://www.w3.org/2000/DD/DC"
  xmlns:omgdi="http://www.omg.org/spec/DD/20100524/DI">
  <collaboration id="Collaboration_1"><participant id="Object1" name="Insurer" processRef="Process_1"/></collaboration>
  <process id="Process_1" isExecutable="false">
    <laneSet id="LaneSet_1"><lane id="Object2" name="Officer"/></laneSet>
    <startEvent id="Object3" name="start"/>
    <task id="Object4" name="Check"/>
    <sequenceFlow id="Object6" sourceRef="Object3" targetRef="Object4"/>
  </process>
  <bpmndi:BPMNDiagram id="BPMNDiagram_1"><bpmndi:BPMNPlane id="BPMNPlane_1" bpmnElement="Collab"/></bpmndi:BPMNDiagram>
</definitions>"""
    xml1, rep = synchronize(elems, broken)
    checks = []
    checks.append(("补建 association", 'id="Object7"' in xml1 and "<association" in xml1.lower()))
    checks.append(("补建 dataObject 并规范化为 reference",
                   "dataObjectReference" in xml1 and 'dataObjectRef="Object5_do"' in xml1))
    checks.append(("pool/lane 补 DI", xml1.count("BPMNShape") >= 4))
    checks.append(("association 补 DI edge", xml1.count("BPMNEdge") >= 2))
    checks.append(("plane 指向 collaboration", 'bpmnElement="Collaboration_1"' in xml1))
    checks.append(("lane.flowNodeRef 补齐", xml1.count("flowNodeRef") >= 2))
    checks.append(("message start event 定义", "messageEventDefinition" in xml1))
    cons = conservation_check(elems, xml1)
    checks.append(("守恒：无缺失", all(not v["missing_ids"] for v in cons.values())))
    checks.append(("DI 全覆盖", di_coverage(xml1)["coverage"] == 1.0))
    xml2, rep2 = synchronize(elems, xml1)
    checks.append(("幂等", xml2 == xml1 and rep2.is_clean()))

    failed = [n for n, ok in checks if not ok]
    for n, ok in checks:
        print(("  ✅ " if ok else "  ❌ ") + n)
    if not failed:
        print("\n自测全部通过")
        return 0
    print("\n失败项：" + ", ".join(failed))
    print(xml1)
    return 1


if __name__ == "__main__":
    sys.exit(main())
