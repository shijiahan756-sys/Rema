import json
import xml.etree.ElementTree as ET

from HallucinationTest.check import check
from config import XML_CORRECT_AGENT_PROMPT_FILE, MULTIAGENT_MODIFY_AGENT_PROMPT_FILE
from file_utils import load_prompt_text


VALIDATION_OK = "ok"
VALIDATION_FAILED = "failed"
VALIDATION_INSUFFICIENT = "insufficient_evidence"

BPMN_NAMESPACES = {
    "bpmn": "http://www.omg.org/spec/BPMN/20100524/MODEL",
    "bpmndi": "http://www.omg.org/spec/BPMN/20100524/DI",
    "dc": "http://www.omg.org/spec/DD/20100524/DC",
    "di": "http://www.omg.org/spec/DD/20100524/DI",
    "xsi": "http://www.w3.org/2001/XMLSchema-instance",
}

def _check_xml_syntax_and_refs(xml_text):
    problems = []
    root = None
    try:
        parser = ET.XMLParser()
        root = ET.fromstring(xml_text, parser=parser)
    except ET.ParseError as e:
        problems.append(f"XML parse error: {e}")
        return problems, None

    expected_root_tag = f"{{{BPMN_NAMESPACES['bpmn']}}}definitions"
    if root.tag != expected_root_tag:
        problems.append(f"Invalid root tag: expected '{expected_root_tag}', got '{root.tag}'.")
        return problems, root


    for flow_tag in ("sequenceFlow", "messageFlow"):
        for flow_elem in root.iter(f"{{{BPMN_NAMESPACES['bpmn']}}}{flow_tag}"):
            flow_id = flow_elem.get("id")
            source_ref = flow_elem.get("sourceRef")
            target_ref = flow_elem.get("targetRef")

            if source_ref is None or not str(source_ref).strip():
                problems.append(f"<{flow_tag} id={flow_id}> missing or blank 'sourceRef' attribute.")
            if target_ref is None or not str(target_ref).strip():
                problems.append(f"<{flow_tag} id={flow_id}> missing or blank 'targetRef' attribute.")


    return problems, root


def _build_correction_messages(bpmn_xml, error_info, warning_info, final_elements_json, correction_system_prompt):
    sections = []
    sections.append(("## Element JSON (preservation context only; no topology or coverage checks)\n" + final_elements_json))
    if error_info:
        sections.append("## Definite Tag, Attribute, Namespaces errors (fix only within this scope)\n" + error_info)
    if warning_info:
        sections.append(
            "## Warnings / unverifiable (DO NOT delete or rename elements just because they appear here; these may be valid extensions)\n"
            + warning_info
            + "\nYou must not perform destructive changes (removing elements / flows) solely because of entries in the warnings section."
        )
    sections.append("## Original BPMN XML\n" + bpmn_xml)
    sections.append("## Output\nEmit ONLY the complete corrected XML. Fix only Tag, Attribute, Namespaces errors supported by the input. Preserve business elements, connections and layout; do not delete or add elements, repair topology, or change layout to satisfy out-of-scope concerns.")
    return [
        {"role": "system", "content": correction_system_prompt},
        {"role": "user", "content": "\n\n".join(sections)}
    ]


def run_validation(bpmn_xml, final_elements_json, call_model_func):
    print("\n--- XML Tag, Attribute, Namespaces validation and correction ---")

    xml_errors, root_elem = _check_xml_syntax_and_refs(bpmn_xml)

    check_crashed = False
    check_crash_msg = ""
    hallucination_errors = ""
    hallucination_warnings = ""
    malformed = False
    try:
        check_instance = check(bpmn_xml)
        hallucination_errors = check_instance.getError() or ""
        hallucination_warnings = check_instance.getWarnings() or ""
        malformed = check_instance.is_malformed_input()
    except Exception as e:
        check_crashed = True
        check_crash_msg = f"HallucinationTest checker exception (program fault, not an XML syntax error): {e}"
        print(f"    [程序故障] {check_crash_msg}")

    error_parts = []
    warning_parts = []

    if xml_errors:
        error_parts.append("## XML Tag, Attribute, Namespace Errors\n" + "\n".join(f"- {e}" for e in xml_errors))

    if hallucination_errors.strip():
        error_parts.append("## HallucinationTest Errors\n" + hallucination_errors.strip())
    if hallucination_warnings.strip():
        warning_parts.append("## HallucinationTest Warnings (unknown or whitelist-missing items)\n" + hallucination_warnings.strip())
    if malformed and not xml_errors:
        error_parts.append("## HallucinationTest Malformed Input\n- No tags could be parsed or input is structurally empty.")

    combined_errors = "\n\n".join(error_parts) if error_parts else ""
    combined_warnings = "\n\n".join(warning_parts) if warning_parts else ""

    print(f"    [hard errors sections] = {bool(combined_errors)} ; [warnings sections] = {bool(combined_warnings)}")
    if combined_warnings:
        print("    Warnings (not triggering destructive correction):")
        for line in combined_warnings.splitlines()[:6]:
            print(f"      {line}")
        if len(combined_warnings.splitlines()) > 6:
            print(f"      ... ({len(combined_warnings.splitlines())-6} more)")
    if combined_errors:
        print("    Hard errors exist, correction request will be issued (warnings appended as non-destructive context only):")
        for line in combined_errors.splitlines()[:6]:
            print(f"      {line}")

    if check_crashed and not combined_errors:
        print("    检查器异常但无确定 XML 错误；不将程序故障发给模型修复。")
        return VALIDATION_FAILED, bpmn_xml

    if not combined_errors:
        print("    Tag, Attribute, Namespaces checks passed. Warnings alone do not trigger automatic correction.")
        return VALIDATION_OK, bpmn_xml

    try:
        correction_system_prompt = load_prompt_text(XML_CORRECT_AGENT_PROMPT_FILE)
        load_prompt_text(MULTIAGENT_MODIFY_AGENT_PROMPT_FILE)
    except Exception as e:
        print(f"读取专用修正提示词失败 (config failure): {e}")
        return VALIDATION_FAILED, bpmn_xml

    messages = _build_correction_messages(
        bpmn_xml, combined_errors, combined_warnings, final_elements_json, correction_system_prompt
    )
    model_ok = True
    try:
        corrected_xml, _ = call_model_func(messages)
    except Exception as e:
        model_ok = False
        print(f"    修正模型调用失败 (model failure): {e}")
        corrected_xml = None

    if not corrected_xml:
        if model_ok:
            print("    修正 Agent 返回空，保留原文并判为校验失败。")
        return VALIDATION_FAILED, bpmn_xml

    print("    已接收到修正 XML，重新校验...")
    recheck_xml_errs, _ = _check_xml_syntax_and_refs(corrected_xml)
    re_err = ""
    re_warn = ""
    try:
        re_check = check(corrected_xml)
        re_err = (re_check.getError() or "").strip()
        re_warn = (re_check.getWarnings() or "").strip()
    except Exception as e:
        print(f"    [程序故障] 修正后 HallucinationTest 重检异常: {e}")

    if recheck_xml_errs:
        print("    修正后 XML Tag, Attribute, Namespace 仍存在问题:")
        for e in recheck_xml_errs[:5]:
            print(f"      - {e}")
    if re_err:
        print(f"    修正后 HallucinationTest 硬错误仍存在: {re_err[:200]}")
    if re_warn and not re_err and not recheck_xml_errs:
        print(f"    修正后只剩 warnings（{len(re_warn.splitlines())} 行）：Tag, Attribute, Namespaces checks passed (warnings alone do not indicate failure). ")

    still_has_errors = bool(recheck_xml_errs) or bool(re_err)
    if still_has_errors:
        print("    结果仍有确定错误，不伪装为已验证通过。")
        status = VALIDATION_FAILED
    else:
        print("    修正后全部硬错误清零。Tag, Attribute, Namespaces checks passed.")
        status = VALIDATION_OK
    return status, corrected_xml
