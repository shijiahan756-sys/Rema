import json
import os

from config import (
    OUTPUT_SUBDIR,
    REVIEW_AGENT_PROMPT_FILE,
    MULTIAGENT_MODIFY_AGENT_PROMPT_FILE,
    RULES_FILE,
    ELEMENTS_SCHEMA_FILE,
    REVIEW_AND_EDIT_ROUNDS,
)
from file_utils import (
    load_prompt_text,
    load_json_file,
    load_schema,
    save_text,
    save_json,
    image_to_base64,
    detect_image_mime,
    get_image_size,
)
from extraction import normalize_connection_refs, ExtractionConflictError


REVIEW_STATUS_PASSED = "passed"
REVIEW_STATUS_REVISION = "needs_revision"
REVIEW_STATUS_EVIDENCE = "needs_evidence"
REVIEW_STATUS_FAILED = "failed"


class ReviewParseError(ValueError):
    pass


class ImageLoadError(RuntimeError):
    pass


def _load_rules(rules_file):
    path = rules_file or RULES_FILE
    try:
        data = load_json_file(path)
    except Exception as e:
        raise RuntimeError(f"读取规则文件失败 (config failure): {e}")
    if not isinstance(data, dict):
        raise RuntimeError("规则文件顶层不是 JSON 对象。")
    constraints = data.get("constraints", [])
    if not isinstance(constraints, list):
        raise RuntimeError("规则文件 constraints 字段不是列表。")
    if constraints and isinstance(constraints[0], dict):
        return constraints
    normalized = []
    for i, rule in enumerate(constraints, start=1):
        normalized.append({"id": f"R{i}", "evidence_required": True, "text": str(rule), "applies_to": "legacy"})
    return normalized


def _load_and_cache_image(image_path):
    if not image_path:
        raise ImageLoadError("image_path 为空。")
    if not os.path.exists(image_path):
        raise ImageLoadError(f"原图不存在: {image_path}")
    try:
        mime = detect_image_mime(image_path)
    except Exception as e:
        raise ImageLoadError(f"原图 MIME 识别失败: {e}")
    if mime not in ("image/png", "image/jpeg"):
        raise ImageLoadError(f"原图格式不支持: {mime}（仅 png/jpeg）。")
    try:
        width, height = get_image_size(image_path)
    except Exception as e:
        raise ImageLoadError(f"原图尺寸读取失败: {e}")
    try:
        b64 = image_to_base64(image_path)
    except Exception as e:
        raise ImageLoadError(f"原图读取失败: {e}")
    data_url = f"data:{mime};base64,{b64}"
    return {
        "path": image_path,
        "mime": mime,
        "width": int(width),
        "height": int(height),
        "data_url": data_url,
    }


def _build_user_multimodal(text_parts, image_cache):
    text = ("".join(text_parts) if isinstance(text_parts, (list, tuple)) else str(text_parts)).strip()
    if image_cache is None:
        return text
    return [
        {"type": "text", "text": text},
        {"type": "image_url", "image_url": {"url": image_cache["data_url"]}},
    ]


def _validate_report_entry(entry, field_name, idx):
    if not isinstance(entry, dict):
        raise ReviewParseError(f"{field_name}[{idx}] 不是对象。")
    if not entry:
        raise ReviewParseError(f"{field_name}[{idx}] 为空对象且未提供 rule_id/message。")


def _parse_review_report(raw):
    if raw is None:
        raise ReviewParseError("Review returned None (null).")
    if isinstance(raw, str) and not raw.strip():
        raise ReviewParseError("Review returned empty string.")
    try:
        report = json.loads(raw) if isinstance(raw, str) else raw
    except json.JSONDecodeError as e:
        raise ReviewParseError(f"Review report is not valid JSON: {e}")

    if report is None:
        raise ReviewParseError("Review report parsed as null.")
    if isinstance(report, list):
        raise ReviewParseError("Review report 顶层为列表 []，必须是 JSON 对象。")
    if not isinstance(report, dict):
        raise ReviewParseError("Review report 顶层不是 JSON 对象。")

    if "errors" not in report:
        raise ReviewParseError("Review report 缺少必需字段 'errors'。")
    if "unverifiable" not in report:
        raise ReviewParseError("Review report 缺少必需字段 'unverifiable'。")

    errors = report.get("errors")
    unverifiable = report.get("unverifiable")

    if errors is None:
        raise ReviewParseError("Review report 'errors' 为 null，必须为列表。")
    if unverifiable is None:
        raise ReviewParseError("Review report 'unverifiable' 为 null，必须为列表。")
    if not isinstance(errors, list):
        raise ReviewParseError("Review report 'errors' 不是列表。")
    if not isinstance(unverifiable, list):
        raise ReviewParseError("Review report 'unverifiable' 不是列表。")

    for i, e in enumerate(errors):
        _validate_report_entry(e, "errors", i)
    for i, u in enumerate(unverifiable):
        _validate_report_entry(u, "unverifiable", i)

    has_errors = len(errors) > 0
    has_unverifiable = len(unverifiable) > 0

    if has_errors:
        computed = REVIEW_STATUS_REVISION
    elif has_unverifiable:
        computed = REVIEW_STATUS_EVIDENCE
    else:
        computed = REVIEW_STATUS_PASSED

    original_status = report.get("status")
    if original_status is not None and original_status != computed:
        print(f"    [报告状态校正] 报告声明 status={original_status!r}，按内容计算为 {computed!r}。")
    report["status"] = computed
    return report


def _consensus_status_after_modify(prev_report, modified_parse_error=None):
    if modified_parse_error is not None:
        return REVIEW_STATUS_REVISION
    if prev_report["errors"]:
        return REVIEW_STATUS_REVISION
    if prev_report["unverifiable"]:
        return REVIEW_STATUS_EVIDENCE
    return REVIEW_STATUS_PASSED


def _build_review_messages(current_elements, rules, schema_text, review_system_prompt,
                           image_cache, findings_text=""):
    image_meta = (
        f"Original image: mime={image_cache['mime']}, size={image_cache['width']}x{image_cache['height']}. "
        "Rule evidence_required=true 表示需结合本图判断；可判定的错漏写入 errors，无法判定的写入 unverifiable。\n\n"
    ) if image_cache is not None else ""

    user_parts = [
        "## Review scope and evidence\n",
        "Both the element JSON AND the original diagram image are provided. Use the image whenever available evidence supports a determination.\n",
        image_meta,
    ]
    if findings_text:
        user_parts += [
            findings_text,
            "\nThese findings were computed by deterministic code BEFORE your review. "
            "Verify each against the image; keep confirmed ones in errors (with your own "
            "evidence wording) and move contradicted ones to unverifiable with the reason. "
            "Do not silently drop them.\n\n",
        ]
    user_parts += [
        "Distinguish:\n"
        "  (a) Extraction mismatch: JSON does not match the visible image.\n"
        "  (b) The image itself MAY violate a rule. Do NOT redraw or invent business elements just to satisfy a rule. Report as unverifiable when uncertain.\n\n"
        "Do not fabricate IDs for unseen elements. If an element has no assigned id, use element_ids=[] and describe its location in the message.\n"
        "Message fields must include: element id(s) (or []), approximate image position when visual, observation basis, and concrete fix supported by evidence.\n\n",
        "## Shared elements schema\n",
        schema_text,
        "\n\n## All constraint rules from rules.json (apply all; evidence_required=true uses the attached image)\n",
        json.dumps(rules, ensure_ascii=False, indent=2),
        "\n\n## Element JSON document to review\n",
        current_elements,
        "\n\n## Output format requirement\n",
        "Reply with a single JSON object: {\"status\": \"passed|needs_revision|needs_evidence\", \"errors\": [...], \"unverifiable\": [...]}. No prose, no Markdown.",
    ]
    return [
        {"role": "system", "content": review_system_prompt},
        {"role": "user", "content": _build_user_multimodal(user_parts, image_cache)},
    ]


def _build_edit_messages(current_elements, report, rules, schema_text, modify_system_prompt):
    user_parts = [
        "## Blind Modify context (NO IMAGE PROVIDED — BLIND MODIFY EXPERIMENT)\n",
        "IMPORTANT: The original diagram image is NOT available to you in this task. "
        "You must rely EXCLUSIVELY on the review report's error entries and your structural consistency of the JSON document.\n",
        "Fix ONLY items listed in review report errors where the review report entry provides enough concrete evidence (observation basis, approximate position from Review Agent's visual inspection, element IDs, explicit suggested fix).\n",
        "Do NOT invent or guess values for entries in report.unverifiable. Do not delete elements solely to suppress an error.\n",
        "Allowed fixes:\n"
        "  - Name/label corrections with explicit textual evidence from the review report (Review report's message)\n"
        "  - Type/class label corrections explicitly stated in review report errors\n"
        "  - Flow direction / sourceRef / targetRef when the review report gives explicit endpoints\n"
        "  - Adding missing elements ONLY when the review report explicitly describes them with enough detail (id suggestion, type, name, position, and connections)\n"
        "  - Structural JSON/schema fixes (connection reference normalization etc.)\n"
        "Preserve ALL IDs and untouched data. NEVER invent a Pool, Lane, Event, Gateway, Activity, DataObject or flow that has no explicit description in the review report errors.\n\n",
        "## Shared elements schema\n",
        f"{schema_text}\n\n",
        "## All constraint rules from rules.json\n",
        f"{json.dumps(rules, ensure_ascii=False, indent=2)}\n\n",
        "## Review report (errors have evidence summary provided by Review Agent; modify only these)\n",
        f"{json.dumps(report, ensure_ascii=False, indent=2)}\n\n",
        "## Current element JSON to modify\n",
        f"{current_elements}\n\n",
        "## Output requirement\n",
        "Reply ONLY with the complete corrected element JSON document (same envelope). NO XML, NO prose, NO Markdown fences. "
        "Leave unverifiable items untouched; inventing visual values you cannot confirm from the report is forbidden.",
    ]
    return [
        {"role": "system", "content": modify_system_prompt},
        {"role": "user", "content": _build_user_multimodal(user_parts, None)},
    ]


def _normalize_elements_text(raw_text):
    if raw_text is None:
        raise ExtractionConflictError("Empty elements text.")
    parsed = json.loads(raw_text)
    normalize_connection_refs(parsed)
    return json.dumps(parsed, ensure_ascii=False, indent=2), parsed


def _dedupe_findings(findings):
    out, seen = [], set()
    for f in findings or []:
        key = (f.get("rule_id"), tuple(f.get("element_ids") or []),
               (f.get("message") or "")[:60])
        if key in seen:
            continue
        out.append(dict(f))
        seen.add(key)
    return out


def collect_review_findings(initial_elements, image_path, call_model_func,
                            ablation_config=None,
                            inventory_errors=None, edge_errors=None,
                            extra_findings=None,
                            output_dir=None, base_name=None):
    ablation = ablation_config or {}
    findings = list(extra_findings or [])
    info = None
    try:
        from review_tools import prepare_findings
        tool_findings, info = prepare_findings(
            initial_elements, image_path, call_model_func,
            ablation=ablation,
            inventory_errors=inventory_errors, edge_errors=edge_errors,
            output_dir=output_dir, base_name=base_name)
        findings = _dedupe_findings(findings + list(tool_findings or []))
    except Exception as e:
        print(f"    [review] 工具层核查失败，仅保留上游 findings: {e}")
    return findings, info


def _review_and_edit_impl(
    initial_elements,
    rules_file,
    call_model_func,
    output_dir,
    base_name,
    rounds,
    image_path,
    extra_findings=None,
    inventory_errors=None,
    edge_errors=None,
    ablation_config=None,
):
    print("--- 步骤 2: 格式审查与修改 (Blind Modify 实验：仅 Review Agent 可见原图，Modify Agent 盲改) ---")

    print("    [review] 审查前置：Review Agent 调用工具层做确定性核查…")
    findings, _tools_info = collect_review_findings(
        initial_elements, image_path, call_model_func,
        ablation_config=ablation_config,
        inventory_errors=inventory_errors, edge_errors=edge_errors,
        extra_findings=extra_findings,
        output_dir=output_dir, base_name=base_name)
    global _last_findings
    _last_findings = findings
    print(f"    [review] 工具层核查完成：findings {len(findings)} 条（已并入本次审查证据）")

    findings_text = ""
    if findings:
        try:
            from review_tools import findings_to_text
            findings_text = findings_to_text(findings)
        except Exception as e:
            print(f"    [review] findings 注入失败（忽略）: {e}")

    save_dir = os.path.join(output_dir, OUTPUT_SUBDIR)
    os.makedirs(save_dir, exist_ok=True)

    try:
        image_cache = _load_and_cache_image(image_path)
    except ImageLoadError as e:
        print(f"步骤 2 原图加载失败，停止后续流程（Review Agent 需要原图）: {e}")
        return initial_elements, REVIEW_STATUS_FAILED

    print(f"    原图已缓存 (仅用于 Review Agent): {image_cache['mime']} {image_cache['width']}x{image_cache['height']} (Modify Agent 将不接收图片)")
    print(f"    [Blind Modify Experiment] Review Agent = 原图 + JSON; Modify Agent = Report(JSON only")

    try:
        rules = _load_rules(rules_file)
    except Exception as e:
        print(f"加载规则失败 (config failure, 与证据不足无关): {e}")
        return initial_elements, REVIEW_STATUS_FAILED

    try:
        schema = load_schema(ELEMENTS_SCHEMA_FILE)
    except Exception as e:
        print(f"加载 schema 失败 (config failure): {e}")
        return initial_elements, REVIEW_STATUS_FAILED
    schema_text = json.dumps(schema, ensure_ascii=False, indent=2)

    try:
        review_system_prompt = load_prompt_text(REVIEW_AGENT_PROMPT_FILE)
        modify_system_prompt = load_prompt_text(MULTIAGENT_MODIFY_AGENT_PROMPT_FILE)
    except Exception as e:
        print(f"读取审查/修改提示词失败 (config failure, 与证据不足无关): {e}")
        return initial_elements, REVIEW_STATUS_FAILED

    try:
        current_text, _ = _normalize_elements_text(initial_elements)
    except (ExtractionConflictError, json.JSONDecodeError) as e:
        print(f"初始提取 JSON 无法规范化 (确定性输入错误，不属于证据不足): {e}")
        return initial_elements, REVIEW_STATUS_REVISION

    latest_status = REVIEW_STATUS_REVISION
    latest_valid_report = None
    candidate_modified_since_review = True

    for i in range(rounds):
        print(f"  [轮次 {i+1}]")

        report = None
        review_invalid_reason = None

        if candidate_modified_since_review:
            print("    正在进行格式审查 (Review Agent 携带原图)...")
            messages_review = _build_review_messages(
                current_text, rules, schema_text, review_system_prompt, image_cache,
                findings_text=findings_text
            )
            try:
                review_raw, _ = call_model_func(messages_review)
            except Exception as e:
                review_invalid_reason = f"review model call failed: {e}"
                review_raw = None
                print(f"    [模型失败] 审查模型调用异常: {e}")

            if review_invalid_reason is None:
                try:
                    report = _parse_review_report(review_raw)
                except ReviewParseError as e:
                    review_invalid_reason = str(e)
                    report_path = save_text(
                        output_dir, OUTPUT_SUBDIR,
                        f"{base_name}_step2_round{i+1}_report_invalid.txt",
                        review_raw or "(None)"
                    )
                    print(f"    审查报告解析失败 (报告无效 ≠ 证据不足): {e}")
                    print(f"    原始审查报告已保存至: {report_path}")
        else:
            print("    候选未修改，跳过重复审查。")
            report = latest_valid_report

        if report is not None:
            latest_valid_report = report
            candidate_modified_since_review = False
            report_path = save_text(
                output_dir, OUTPUT_SUBDIR,
                f"{base_name}_step2_round{i+1}_report.json",
                json.dumps(report, ensure_ascii=False, indent=2)
            )
            print(f"    审查报告已保存至: {report_path}")
            print(f"    审查状态: {report['status']} | errors={len(report['errors'])} | unverifiable={len(report['unverifiable'])}")

            if report["status"] == REVIEW_STATUS_PASSED:
                latest_status = REVIEW_STATUS_PASSED
                print("    审查通过，无需修改。")
                break

            if not report["errors"]:
                print("    errors 为空，跳过修改 Agent (仅证据不足不触发修改)。")
                latest_status = _consensus_status_after_modify(report, None)
                break
        else:
            latest_status = REVIEW_STATUS_REVISION if review_invalid_reason else latest_status

        print("    正在根据报告进行修改 (Modify Agent 盲改 — 不接收原图)...")
        if report is None:
            print("    缺少有效报告，修改无法进行，保留原候选。")
            continue

        messages_edit = _build_edit_messages(
            current_text, report, rules, schema_text, modify_system_prompt
        )
        modify_parse_err = None
        model_call_ok = True
        try:
            modified_raw, _ = call_model_func(messages_edit)
        except Exception as e:
            model_call_ok = False
            modify_parse_err = f"modify model call failed: {e}"
            modified_raw = None
            print(f"    [模型失败] 修改模型调用异常: {e}")

        accepted_new_candidate = False
        if modified_raw:
            try:
                normalized_text, _ = _normalize_elements_text(modified_raw)
                if normalized_text != current_text:
                    current_text = normalized_text
                    accepted_new_candidate = True
                    candidate_modified_since_review = True
                mod_path = save_text(
                    output_dir, OUTPUT_SUBDIR,
                    f"{base_name}_step2_round{i+1}_modified.json",
                    current_text
                )
                verb = "接受并更新" if accepted_new_candidate else "通过解析但内容未变更"
                print(f"    第 {i+1} 轮修改{verb}，已保存至: {mod_path}")
            except (ExtractionConflictError, json.JSONDecodeError) as e:
                modify_parse_err = str(e)
                invalid_path = save_json(
                    output_dir, None,
                    os.path.join(save_dir, f"{base_name}_step2_round{i+1}_modified_invalid.json"),
                    {"parse_error": str(e), "raw": modified_raw}
                )
                print(f"    修改结果解析失败，保留原候选: {invalid_path}")
        else:
            if model_call_ok:
                modify_parse_err = "modify model returned empty"
                print("    修改 Agent 返回空，保留原候选。")

        latest_status = _consensus_status_after_modify(report, modify_parse_err)

    perform_final_review = False
    if latest_valid_report is None:
        print("  [最终复审] 从未获得有效审查报告，补一次带原图审查...")
        perform_final_review = True
    elif candidate_modified_since_review:
        print("  [最终复审] 候选自上次有效审查后已修改，再做一次带原图审查...")
        perform_final_review = True
    elif latest_valid_report["status"] != REVIEW_STATUS_PASSED and rounds > 0:
        print("  [最终复审] 对最后候选做一次带原图终审...")
        perform_final_review = True

    if perform_final_review:
        msgs = _build_review_messages(current_text, rules, schema_text, review_system_prompt,
                                      image_cache, findings_text=findings_text)
        final_failed = False
        try:
            raw2, _ = call_model_func(msgs)
        except Exception as e:
            print(f"    [模型失败] 最终复审模型调用异常: {e}")
            final_failed = True
            raw2 = None
        if not final_failed:
            try:
                final_rep = _parse_review_report(raw2)
                latest_valid_report = final_rep
                latest_status = final_rep["status"]
                candidate_modified_since_review = False
                save_text(
                    output_dir, OUTPUT_SUBDIR,
                    f"{base_name}_step2_final_review.json",
                    json.dumps(final_rep, ensure_ascii=False, indent=2)
                )
                print(f"    最终复审状态: {final_rep['status']} | errors={len(final_rep['errors'])} | unverifiable={len(final_rep['unverifiable'])}")
            except ReviewParseError as e:
                final_failed = True
                print(f"    最终复审解析失败，不伪装成审查通过: {e}")
        if final_failed:
            latest_status = REVIEW_STATUS_REVISION

    if latest_valid_report is None:
        print("  全程未获得有效审查报告，不宣称通过。")
        return current_text, REVIEW_STATUS_REVISION

    if latest_valid_report["errors"]:
        latest_status = REVIEW_STATUS_REVISION
    elif latest_valid_report.get("unverifiable"):
        latest_status = REVIEW_STATUS_EVIDENCE
    else:
        latest_status = REVIEW_STATUS_PASSED

    return current_text, latest_status


def review_and_edit(
    initial_elements,
    rules_file,
    call_model_func,
    output_dir,
    base_name,
    rounds=REVIEW_AND_EDIT_ROUNDS,
    image_path=None,
    extra_findings=None,
    inventory_errors=None,
    edge_errors=None,
    ablation_config=None,
):
    global _last_findings
    _last_findings = []
    text, status = _review_and_edit_impl(
        initial_elements,
        rules_file or RULES_FILE,
        call_model_func,
        output_dir,
        base_name,
        rounds,
        image_path,
        extra_findings=extra_findings,
        inventory_errors=inventory_errors,
        edge_errors=edge_errors,
        ablation_config=ablation_config,
    )
    global _last_review_status
    _last_review_status = status
    return text


_last_review_status = None
_last_findings = []


def get_last_review_status():
    return _last_review_status


def get_last_findings():
    return _last_findings
