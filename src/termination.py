from __future__ import annotations

import json
import xml.etree.ElementTree as ET

import bpmn_geometry as geo

BPMN = "{http://www.omg.org/spec/BPMN/20100524/MODEL}"
BPMNDI = "{http://www.omg.org/spec/BPMN/20100524/DI}"
DDDI = "{http://www.omg.org/spec/DD/20100524/DI}"
DC = "{http://www.omg.org/spec/DD/20100524/DC}"


def get_diagram_completeness(elements):
    try:
        envelope = json.loads(elements) if isinstance(elements, str) else elements
    except Exception:
        return {"startEvent": "present", "endEvent": "present", "evidence": ""}
    dc = envelope.get("diagramCompleteness") or {}
    return {
        "startEvent": dc.get("startEvent", "present"),
        "endEvent": dc.get("endEvent", "present"),
        "evidence": str(dc.get("evidence", ""))[:300],
    }


def _ids(root):
    return {e.get("id") for e in root.iter() if e.get("id")}


def _unique_id(root, base):
    ids = _ids(root)
    if base not in ids:
        return base
    i = 1
    while f"{base}_{i}" in ids:
        i += 1
    return f"{base}_{i}"


def _has_end_event(root):
    return any(True for _ in root.iter(BPMN + "endEvent"))


def _activities_without_outgoing(root):
    outs = set()
    for f in root.iter(BPMN + "sequenceFlow"):
        outs.add(f.get("sourceRef"))
    result = []
    for el in root.iter():
        if not isinstance(el.tag, str):
            continue
        t = el.tag.rsplit("}", 1)[-1]
        if t in geo.ACTIVITY_TAGS + geo.GATEWAY_TAGS and el.get("id") not in outs:
            result.append(el)
    return result


def _append_flow(root, taken, flow_id, src_id, dst_id, note):
    proc = next(root.iter(BPMN + "process"), None)
    if proc is None:
        return None
    f = ET.SubElement(proc, BPMN + "sequenceFlow")
    f.set("id", _unique_id(root, flow_id))
    f.set("sourceRef", src_id)
    f.set("targetRef", dst_id)
    doc = ET.SubElement(f, BPMN + "documentation")
    doc.text = note
    return f.get("id")


def apply_termination_policy(bpmn_xml, elements, policy="normalized"):
    report = {"policy": policy, "completeness": get_diagram_completeness(elements),
              "added_end_event": None, "added_flow": None,
              "needs_human_review": False, "documentation_added": False}
    if policy not in ("faithful", "normalized", "interactive"):
        policy = "normalized"
        report["policy"] = policy
    try:
        root = ET.fromstring(bpmn_xml)
    except ET.ParseError as e:
        report["error"] = f"parse error: {e}"
        return bpmn_xml, report

    dc = report["completeness"]
    end_declared_absent = dc.get("endEvent") in ("absent_ellipsis", "absent_unclear")
    has_end = _has_end_event(root)

    if end_declared_absent and not has_end:
        if policy == "interactive":
            report["needs_human_review"] = True
            report["note"] = "diagram ends with ellipsis; end event left to human review"
            return bpmn_xml, report

        tails = _activities_without_outgoing(root)
        tail = next((el for el in tails
                     if el.tag.rsplit("}", 1)[-1] in geo.ACTIVITY_TAGS), None)
        doc_note = ("source diagram ends with ellipsis; no end event drawn"
                    if dc.get("endEvent") == "absent_ellipsis"
                    else "end event not visible in source diagram")
        proc = next(root.iter(BPMN + "process"), None)
        if proc is None:
            report["error"] = "no <process> element"
            return bpmn_xml, report

        changed = True
        proc_doc = proc.find(BPMN + "documentation")
        if proc_doc is None:
            proc_doc = ET.SubElement(proc, BPMN + "documentation")
            proc.remove(proc_doc)
            proc.insert(0, proc_doc)
        proc_doc.text = ((proc_doc.text or "") +
                         f" [termination: {doc_note}]").strip()
        report["documentation_added"] = True

        changed = False
        if policy == "normalized" and tail is not None:
            taken = set()
            end_id = _unique_id(root, "AutoEndEvent_1")
            end_el = ET.SubElement(proc, BPMN + "endEvent")
            end_el.set("id", end_id)
            end_doc = ET.SubElement(end_el, BPMN + "documentation")
            end_doc.text = "auto-completed end event (" + doc_note + ")"
            fid = _append_flow(root, taken, "AutoEndFlow_1", tail.get("id"), end_id,
                               "auto-completed: connect trailing activity to end event")
            plane = next(root.iter(BPMNDI + "BPMNPlane"), None)
            if plane is not None:
                src_shape = None
                for s in root.iter(BPMNDI + "BPMNShape"):
                    if s.get("bpmnElement") == tail.get("id"):
                        src_shape = s
                        break
                b = src_shape.find(DC + "Bounds") if src_shape is not None else None
                x = (float(b.get("x")) + float(b.get("width")) + 80) if b is not None else 100.0
                y = (float(b.get("y")) + float(b.get("height")) / 2 - 18) if b is not None else 100.0
                dia = next(root.iter(BPMNDI + "BPMNDiagram"), None)
                if dia is None:
                    dia = ET.SubElement(root, BPMNDI + "BPMNDiagram")
                    dia.set("id", _unique_id(root, "BPMNDiagram_1"))
                pl = dia.find(BPMNDI + "BPMNPlane")
                if pl is None:
                    pl = ET.SubElement(dia, BPMNDI + "BPMNPlane")
                shape = ET.SubElement(pl, BPMNDI + "BPMNShape")
                shape.set("id", "BPMNShape_" + end_id)
                shape.set("bpmnElement", end_id)
                bounds = ET.SubElement(shape, DC + "Bounds")
                bounds.set("x", f"{x:.0f}")
                bounds.set("y", f"{y:.0f}")
                bounds.set("width", "36")
                bounds.set("height", "36")
                if fid is not None:
                    edge = ET.SubElement(pl, BPMNDI + "BPMNEdge")
                    edge.set("id", "BPMNEdge_" + (fid or "auto"))
                    edge.set("bpmnElement", fid)
                    sx, sy = float(b.get("x")) + float(b.get("width")), \
                        float(b.get("y")) + float(b.get("height")) / 2
                    for px, py in ((sx, sy), (x, y + 18)):
                        wp = ET.SubElement(edge, DDDI + "waypoint")
                        wp.set("x", f"{px:.0f}")
                        wp.set("y", f"{py:.0f}")
            report["added_end_event"] = end_id
            report["added_flow"] = fid
            print(f"    [termination] normalized: 自动补终止事件 {end_id}（documentation 已标注）")
        elif policy == "faithful":
            print("    [termination] faithful: 不补终止事件，仅在 documentation 声明")
        else:
            changed = False
    if not report["documentation_added"] and not report["added_end_event"]:
        return bpmn_xml, report
    return ET.tostring(root, encoding="unicode"), report


def self_test():
    xml = """<definitions xmlns="http://www.omg.org/spec/BPMN/20100524/MODEL"
      xmlns:bpmndi="http://www.omg.org/spec/BPMN/20100524/DI"
      xmlns:dc="http://www.omg.org/spec/DD/20100524/DC"
      xmlns:di="http://www.omg.org/spec/DD/20100524/DI">
      <process id="p"><task id="t1"/><task id="t2"/>
        <sequenceFlow id="f1" sourceRef="t1" targetRef="t2"/>
      </process>
      <bpmndi:BPMNDiagram id="d"><bpmndi:BPMNPlane id="pl" bpmnElement="p">
        <bpmndi:BPMNShape id="s1" bpmnElement="t1"><dc:Bounds x="0" y="0" width="100" height="60"/></bpmndi:BPMNShape>
        <bpmndi:BPMNShape id="s2" bpmnElement="t2"><dc:Bounds x="200" y="0" width="100" height="60"/></bpmndi:BPMNShape>
      </bpmndi:BPMNPlane></bpmndi:BPMNDiagram></definitions>"""
    elements = json.dumps({"elements": [], "diagramCompleteness": {
        "startEvent": "present", "endEvent": "absent_ellipsis",
        "evidence": "trailing dots at right edge"}})

    out, rep = apply_termination_policy(xml, elements, "normalized")
    assert rep["added_end_event"], rep
    assert "AutoEndEvent_1" in out and "documentation" in out
    assert "bpmnElement=\"AutoEndEvent_1\"" in out
    out2, rep2 = apply_termination_policy(xml, elements, "faithful")
    assert not rep2["added_end_event"] and rep2["documentation_added"]
    out3, rep3 = apply_termination_policy(xml, elements, "interactive")
    assert rep3["needs_human_review"]
    out4, rep4 = apply_termination_policy(xml, json.dumps({"elements": []}), "normalized")
    assert not rep4["added_end_event"] and not rep4["documentation_added"]
    out5, rep5 = apply_termination_policy(out, elements, "normalized")
    assert not rep5["added_end_event"]
    print("termination self-test OK")


if __name__ == "__main__":
    self_test()
