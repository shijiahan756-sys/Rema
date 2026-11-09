from __future__ import annotations

import xml.etree.ElementTree as ET

import bpmn_geometry as geo

BPMN = "{http://www.omg.org/spec/BPMN/20100524/MODEL}"
BPMNDI = "{http://www.omg.org/spec/BPMN/20100524/DI}"
DDDI = "{http://www.omg.org/spec/DD/20100524/DI}"
DC = "{http://www.omg.org/spec/DD/20100524/DC}"

SHAPE_CLASSES = ("Pool", "Lane") + geo.FLOW_NODES + geo.ARTIFACTS
CONN_TAGS = ("sequenceFlow", "messageFlow", "association")


def _issue(rule_id, severity, layer, message, element_ids=None, fix_hint=""):
    return {"rule_id": rule_id, "severity": severity, "layer": layer,
            "message": message, "element_ids": element_ids or [],
            "fix_hint": fix_hint}


def check_ref_existence(envelope):
    items = dict(geo.iter_elements(envelope))
    issues = []
    for eid, body in geo.iter_elements(envelope):
        for field in ("sourceRef", "targetRef", "parent"):
            ref = body.get(field)
            if ref and ref not in items:
                issues.append(_issue(
                    "R12", "error", "json",
                    f"{eid}.{field} -> '{ref}' does not exist",
                    [eid], f"point {field} to an existing ObjectN or remove the field"))
        for field in ("contains", "association", "incoming", "outgoing"):
            for ref in (body.get(field) or []):
                if isinstance(ref, str) and ref not in items:
                    issues.append(_issue(
                        "R12", "error", "json",
                        f"{eid}.{field} -> '{ref}' does not exist",
                        [eid], "remove or fix the dangling reference"))
    return issues


def check_connectivity(envelope):
    issues = []
    for nid, v in geo.dangling_nodes(envelope).items():
        if v["missing"] in ("start_has_incoming", "end_has_outgoing"):
            issues.append(_issue(
                "R16", "error", "json",
                f"{nid} is a {v.get('subType', 'Event')} but has "
                f"{'incoming' if v['missing'] == 'start_has_incoming' else 'outgoing'} flow",
                [nid], "remove the illegal flow or change the event type"))
        else:
            issues.append(_issue(
                "R16", "error", "json",
                f"{nid} ('{v.get('name') or v.get('class')}') is missing {v['missing']} flow",
                [nid], "add the missing flow (edge completion may supply candidates)"))
    return issues


def check_pool_semantics(envelope):
    node_pool = _pool_map(envelope)
    items = dict(geo.iter_elements(envelope))
    issues = []
    for eid, body in geo.iter_elements(envelope):
        cls = body.get("class")
        if cls == "SequenceFlow":
            ps, pt = node_pool.get(body.get("sourceRef")), node_pool.get(body.get("targetRef"))
            if ps and pt and ps != pt:
                issues.append(_issue(
                    "R8", "error", "json",
                    f"SequenceFlow {eid} crosses Pool boundary ({ps} -> {pt})",
                    [eid], "use a MessageFlow for cross-pool communication"))
        elif cls == "MessageFlow":
            ps, pt = node_pool.get(body.get("sourceRef")), node_pool.get(body.get("targetRef"))
            if ps and pt and ps == pt:
                issues.append(_issue(
                    "R9", "error", "json",
                    f"MessageFlow {eid} connects elements inside the same Pool ({ps})",
                    [eid], "use a SequenceFlow inside one pool"))
    return issues


def check_gateway_closure(envelope):
    items = dict(geo.iter_elements(envelope))
    deg = geo.node_degrees(envelope)
    issues = []
    for eid, body in geo.iter_elements(envelope):
        if body.get("class") != "Gateway":
            continue
        outs = deg.get(eid, {}).get("out", [])
        if len(outs) < 2:
            continue
        ends = set()
        for fid in outs:
            flow = items.get(fid, {})
            tgt = flow.get("targetRef")
            ends.add(_branch_end(tgt, items, deg, seen=set()))
        kinds = {e[1] for e in ends if e is not None}
        if len(ends) > 1 and len(kinds - {"end"}) > 1:
            issues.append(_issue(
                "R7", "warning", "json",
                f"Gateway {eid} split branches do not converge on a same-type join "
                f"(branch ends: {sorted(str(e) for e in ends)})",
                [eid], "check whether a join gateway is missing or mis-typed"))
    return issues


def _branch_end(node_id, items, deg, seen, depth=0):
    if node_id is None or depth > 24 or node_id in seen:
        return None
    seen.add(node_id)
    body = items.get(node_id, {})
    if body.get("class") == "Gateway":
        return (node_id, body.get("gatewayType") or "Gateway")
    if body.get("class") == "Event":
        return (node_id, "end" if body.get("subType") == "EndEvent" else "event")
    outs = deg.get(node_id, {}).get("out", [])
    if not outs:
        return (node_id, "dangling")
    for fid in outs:
        tgt = items.get(fid, {}).get("targetRef")
        r = _branch_end(tgt, items, deg, seen, depth + 1)
        if r is not None:
            return r
    return None


def check_supporting_anchoring(envelope):
    items = dict(geo.iter_elements(envelope))
    anchored = set()
    for _eid, body in items.items():
        if body.get("class") == "Association":
            anchored.update(r for r in (body.get("sourceRef"), body.get("targetRef")) if r)
    issues = []
    for eid, body in items.items():
        if body.get("class") in geo.ARTIFACTS and eid not in anchored:
            issues.append(_issue(
                "R17", "error", "json",
                f"artifact {eid} ('{body.get('name') or 'unnamed'}') has no Association",
                [eid], "add the missing dotted line to its host activity"))
    return issues


def _pool_map(envelope):
    pools, lanes, contains = {}, {}, {}
    for eid, body in geo.iter_elements(envelope):
        cls = body.get("class")
        if cls == "Pool":
            pools[eid] = [c for c in (body.get("contains") or []) if isinstance(c, str)]
        elif cls == "Lane":
            lanes[eid] = body.get("parent")
            contains[eid] = [c for c in (body.get("contains") or []) if isinstance(c, str)]
    node_pool = {}
    for pid, members in pools.items():
        for m in members:
            node_pool[m] = pid
    for lid, members in contains.items():
        for m in members:
            node_pool.setdefault(m, lanes.get(lid))
    for nid, body in geo.iter_elements(envelope):
        if nid not in node_pool and body.get("parent"):
            node_pool[nid] = body["parent"]
    return node_pool


def run_json_checks(envelope):
    issues = []
    for fn in (check_ref_existence, check_connectivity, check_pool_semantics,
               check_gateway_closure, check_supporting_anchoring):
        try:
            issues.extend(fn(envelope))
        except Exception as e:
            issues.append(_issue("CHECKER", "warning", "json",
                                 f"{fn.__name__} crashed: {e}", [], ""))
    return issues


def _local(tag):
    return tag.rsplit("}", 1)[-1] if isinstance(tag, str) else ""


def check_xml_ref_existence(root):
    ids = {e.get("id") for e in root.iter() if e.get("id")}
    issues = []
    for tag in CONN_TAGS:
        for el in root.iter(BPMN + tag):
            for field in ("sourceRef", "targetRef"):
                ref = el.get(field)
                if ref and ref not in ids:
                    issues.append(_issue(
                        "R12", "error", "xml",
                        f"<{tag} id={el.get('id')}> {field}='{ref}' does not exist",
                        [el.get("id") or ""],
                        "renderers silently drop such edges; point it at an existing id"))
    for el in root.iter(BPMN + "dataObjectReference"):
        ref = el.get("dataObjectRef")
        if ref and ref not in ids:
            issues.append(_issue(
                "R12", "error", "xml",
                f"<dataObjectReference id={el.get('id')}> dataObjectRef='{ref}' missing",
                [el.get("id") or ""], "create the paired dataObject"))
    return issues


def check_xml_topology(root):
    issues = []
    flow_by_id = {}
    for tag in CONN_TAGS:
        for el in root.iter(BPMN + tag):
            if el.get("id"):
                flow_by_id[el.get("id")] = el
    for el in root.iter():
        if not isinstance(el.tag, str) or not el.tag.startswith(BPMN):
            continue
        t = _local(el.tag)
        inc = [c.text for c in el.findall(BPMN + "incoming") if c.text]
        out = [c.text for c in el.findall(BPMN + "outgoing") if c.text]
        for ref in inc + out:
            if ref not in flow_by_id:
                issues.append(_issue(
                    "R16", "error", "xml",
                    f"<{t} id={el.get('id')}> references flow '{ref}' which does not exist",
                    [el.get("id") or ""], "sync incoming/outgoing with actual flows"))
        if t in geo.ACTIVITY_TAGS + geo.GATEWAY_TAGS:
            if not inc:
                issues.append(_issue("R16", "warning", "xml",
                                     f"<{t} id={el.get('id')}> has no incoming",
                                     [el.get("id") or ""], ""))
            if not out:
                issues.append(_issue("R16", "warning", "xml",
                                     f"<{t} id={el.get('id')}> has no outgoing",
                                     [el.get("id") or ""], ""))
    return issues


def check_di_consistency(root):
    issues = []
    shapes = {s.get("bpmnElement") for s in root.iter(BPMNDI + "BPMNShape")
              if s.get("bpmnElement")}
    edges = {e.get("bpmnElement") for e in root.iter(BPMNDI + "BPMNEdge")
             if e.get("bpmnElement")}
    semantic_shapes = 0
    for el in root.iter():
        if not isinstance(el.tag, str) or not el.tag.startswith(BPMN):
            continue
        t = _local(el.tag)
        eid = el.get("id")
        if not eid:
            continue
        if t in ("participant", "lane") or t in geo.ACTIVITY_TAGS + geo.EVENT_TAGS + \
                geo.GATEWAY_TAGS + geo.DATA_TAGS:
            semantic_shapes += 1
            if t == "dataObject":
                continue
            if eid not in shapes:
                issues.append(_issue(
                    "R18", "error", "xml",
                    f"<{t} id={eid}> has no BPMNShape (invisible when rendered)",
                    [eid], "add BPMNShape with Bounds from the element coordinates"))
        elif t in CONN_TAGS:
            if eid not in edges:
                issues.append(_issue(
                    "R18", "error", "xml",
                    f"<{t} id={eid}> has no BPMNEdge (invisible when rendered)",
                    [eid], "add BPMNEdge with >=2 waypoints"))
    if semantic_shapes and not shapes and not edges:
        issues.append(_issue(
            "RC-DI", "error", "xml",
            "semantic layer exists but the BPMNDiagram section is missing/nonstandard "
            "(often a fabricated DI namespace) - the file renders blank",
            [], "rebuild the DI section deterministically"))
    for s in root.iter(BPMNDI + "BPMNShape"):
        b = s.find(DC + "Bounds")
        if s.get("bpmnElement") and b is None:
            issues.append(_issue(
                "R18", "error", "xml",
                f"BPMNShape for {s.get('bpmnElement')} has no Bounds",
                [s.get("bpmnElement")], ""))
    return issues


def run_xml_checks(xml_text):
    issues = []
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as e:
        return [_issue("XML", "error", "xml", f"parse error: {e}", [], "")], None
    for fn in (check_xml_ref_existence, check_xml_topology, check_di_consistency):
        try:
            issues.extend(fn(root))
        except Exception as e:
            issues.append(_issue("CHECKER", "warning", "xml",
                                 f"{fn.__name__} crashed: {e}", [], ""))
    return issues, root


def to_review_errors(issues):
    out = []
    for i in issues:
        if i["severity"] != "error" or i["layer"] != "json":
            continue
        rid = i["rule_id"] if i["rule_id"].startswith("R") else "RC2"
        out.append({"rule_id": rid, "element_ids": i["element_ids"],
                    "field": i["layer"], "message": i["message"] +
                    (f" Fix hint: {i['fix_hint']}" if i["fix_hint"] else "")})
    return out


def to_validation_sections(issues):
    errs = [i for i in issues if i["severity"] == "error"]
    warns = [i for i in issues if i["severity"] == "warning"]
    err_text = "\n".join(f"- [{i['rule_id']}/{i['layer']}] {i['message']}" for i in errs)
    warn_text = "\n".join(f"- [{i['rule_id']}/{i['layer']}] {i['message']}" for i in warns)
    return err_text, warn_text


def run_all(envelope, xml_text=None):
    result = {"json": run_json_checks(envelope)}
    result["xml"] = []
    if xml_text:
        result["xml"], _root = run_xml_checks(xml_text)
    result["counts"] = {
        "json_errors": sum(1 for i in result["json"] if i["severity"] == "error"),
        "json_warnings": sum(1 for i in result["json"] if i["severity"] == "warning"),
        "xml_errors": sum(1 for i in result["xml"] if i["severity"] == "error"),
        "xml_warnings": sum(1 for i in result["xml"] if i["severity"] == "warning"),
    }
    return result


def self_test():
    env = {"elements": [
        {"P": {"class": "Pool", "x": 0, "y": 0, "width": 600, "height": 300,
               "contains": ["L1"]}},
        {"L1": {"class": "Lane", "parent": "P", "x": 0, "y": 0, "width": 600,
                "height": 300, "contains": ["S", "A", "E"]}},
        {"S": {"class": "Event", "subType": "StartEvent", "x": 0, "y": 0,
               "width": 36, "height": 36}},
        {"A": {"class": "Activity", "x": 100, "y": 0, "width": 100, "height": 50}},
        {"E": {"class": "Event", "subType": "EndEvent", "x": 300, "y": 0,
               "width": 36, "height": 36}},
        {"F1": {"class": "SequenceFlow", "sourceRef": "S", "targetRef": "A"}},
        {"F2": {"class": "SequenceFlow", "sourceRef": "A", "targetRef": "GHOST"}},
        {"D": {"class": "DataObject", "x": 500, "y": 0, "width": 40, "height": 50}},
    ]}
    issues = run_json_checks(env)
    msgs = [i["message"] for i in issues if i["severity"] == "error"]
    assert any("GHOST" in m for m in msgs), msgs
    assert any("R17" == i["rule_id"] and "D" in i["element_ids"] for i in issues), msgs
    env["elements"].append({"A2": {"class": "Activity", "x": 100, "y": 200,
                                   "width": 100, "height": 50}})
    issues = run_json_checks(env)
    assert any(i["rule_id"] == "R16" and "A2" in i["element_ids"] for i in issues)

    xml = """<definitions xmlns="http://www.omg.org/spec/BPMN/20100524/MODEL"
      xmlns:bpmndi="http://www.omg.org/spec/BPMN/20100524/DI"
      xmlns:dc="http://www.omg.org/spec/DD/20100524/DC"
      xmlns:di="http://www.omg.org/spec/DD/20100524/DI">
      <process id="p"><startEvent id="s"/><task id="t"/><endEvent id="e"/>
        <sequenceFlow id="f1" sourceRef="s" targetRef="t"/>
        <sequenceFlow id="f2" sourceRef="t" targetRef="GHOST"/>
      </process>
      <bpmndi:BPMNDiagram id="d"><bpmndi:BPMNPlane id="pl" bpmnElement="p">
        <bpmndi:BPMNShape id="sh_s" bpmnElement="s"><dc:Bounds x="0" y="0" width="36" height="36"/></bpmndi:BPMNShape>
        <bpmndi:BPMNShape id="sh_t" bpmnElement="t"><dc:Bounds x="100" y="0" width="100" height="80"/></bpmndi:BPMNShape>
      </bpmndi:BPMNPlane></bpmndi:BPMNDiagram></definitions>"""
    xissues, root = run_xml_checks(xml)
    xmsgs = [i["message"] for i in xissues if i["severity"] == "error"]
    assert any("GHOST" in m for m in xmsgs), xmsgs
    assert any("no BPMNShape" in m and "e" in m for m in xmsgs), xmsgs
    assert any("no BPMNEdge" in m for m in xmsgs), xmsgs
    xml2 = xml.split("<bpmndi:BPMNDiagram")[0] + "</definitions>"
    i2, _ = run_xml_checks(xml2)
    assert any(i["rule_id"] == "RC-DI" for i in i2), i2
    print("topology_checks self-test OK")


if __name__ == "__main__":
    self_test()
