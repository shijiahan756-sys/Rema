import os
import json

from config import (
    EXTRACT_AGENT_PROMPT_FILE,
    KNOWLEDGE_FILE,
    ELEMENTS_SCHEMA_FILE,
    REFERENCE_FILES,
    REFERENCE_LABELS,
)
from file_utils import (
    image_to_base64,
    detect_image_mime,
    get_image_size,
    load_prompt_text,
    load_plain_text,
    load_schema,
    save_result,
    save_json,
)


class ExtractionConflictError(ValueError):
    pass


def _unwrap_scalar(arr_or_scalar, field_name, element_id):
    if isinstance(arr_or_scalar, list):
        if len(arr_or_scalar) > 1:
            raise ExtractionConflictError(
                f"Element {element_id} {field_name} has multiple references (len={len(arr_or_scalar)}); refusing to choose silently."
            )
        if len(arr_or_scalar) == 0:
            return None
        return arr_or_scalar[0]
    return arr_or_scalar


def normalize_connection_refs(elements_root):
    if not isinstance(elements_root, dict) or "elements" not in elements_root:
        raise ExtractionConflictError("Elements envelope missing (expected {\"elements\": [...]})")
    errors = []
    for entry in elements_root["elements"]:
        if not isinstance(entry, dict) or len(entry) != 1:
            errors.append(f"Invalid element entry shape: {entry!r}")
            continue
        eid, elem = next(iter(entry.items()))
        if not isinstance(elem, dict) or elem.get("class") not in ("SequenceFlow", "MessageFlow", "Association"):
            continue
        src = _unwrap_scalar(elem.get("sourceRef"), "sourceRef", eid)
        tgt = _unwrap_scalar(elem.get("targetRef"), "targetRef", eid)
        inc = _unwrap_scalar(elem.get("incoming"), "incoming", eid)
        out = _unwrap_scalar(elem.get("outgoing"), "outgoing", eid)
        if src is None and inc is None:
            errors.append(f"{eid} connection missing source (no sourceRef/incoming).")
        if tgt is None and out is None:
            errors.append(f"{eid} connection missing target (no targetRef/outgoing).")
        if src is not None and inc is not None and src != inc:
            errors.append(f"{eid} sourceRef {src!r} contradicts incoming {inc!r}.")
        if tgt is not None and out is not None and tgt != out:
            errors.append(f"{eid} targetRef {tgt!r} contradicts outgoing {out!r}.")
        final_src = src if src is not None else inc
        final_tgt = tgt if tgt is not None else out
        if isinstance(elem.get("sourceRef"), list):
            elem["sourceRef"] = final_src
        elif final_src is not None:
            elem["sourceRef"] = final_src
        if isinstance(elem.get("targetRef"), list):
            elem["targetRef"] = final_tgt
        elif final_tgt is not None:
            elem["targetRef"] = final_tgt
    if errors:
        raise ExtractionConflictError("\n".join(errors))
    return elements_root


def _build_extraction_system_prompt(ablation_config=None):
    ablation = ablation_config or {"knowledge_injection": True}
    role_prompt = load_prompt_text(EXTRACT_AGENT_PROMPT_FILE)
    schema = load_schema(ELEMENTS_SCHEMA_FILE)
    schema_text = json.dumps(schema, ensure_ascii=False, indent=2)
    if ablation.get("knowledge_injection", True):
        knowledge = load_plain_text(KNOWLEDGE_FILE, "Knowledge")
        knowledge_section = (
            "## BPMN Recognition Knowledge (applies to extraction stage only)\n"
            f"{knowledge}\n\n"
        )
    else:
        knowledge_section = (
            "## BPMN Recognition Knowledge\n"
            "[ABLATION: knowledge_injection=false] No BPMN element/connection knowledge "
            "will be injected into this prompt. Keep structured output constraint unchanged.\n\n"
        )
    return (
        f"{role_prompt}\n\n"
        "## Shared Elements Schema\n"
        "Use the following JSON schema as the authoritative structure of your output envelope, required fields, enum types, unknown value policy, and legacy field compatibility.\n"
        f"{schema_text}\n\n"
        f"{knowledge_section}"
        "End of system instructions. Await user task and image inputs."
    )


def _build_extraction_user_message(image_path, reference_dir, width, height, mime, ablation_config=None):
    ablation = ablation_config or {"visual_reference": True}
    base64_image = image_to_base64(image_path)
    data_url = f"data:{mime};base64,{base64_image}"

    user_content = [
        {
            "type": "text",
            "text": (
                "## Task\n"
                "Recognize ALL visible BPMN elements in the main image and output a structured JSON document "
                "following the shared elements schema. Do NOT invent elements, labels or directions not supported by the diagram.\n\n"
                "## Main image geometry\n"
                f"File format MIME: {mime}\n"
                f"Main image width (pixels, range for x): {width}\n"
                f"Main image height (pixels, range for y): {height}\n"
                "Coordinate origin is the image top-left. All shape x/y/width/height must be original image pixels, all connection start/end points must be original image pixels located on the attached element boundary.\n"
            )
        },
        {"type": "image_url", "image_url": {"url": data_url}}
    ]

    if ablation.get("visual_reference", True):
        has_reference = False
        for ref_file in REFERENCE_FILES:
            ref_path = os.path.join(reference_dir, ref_file)
            if not os.path.exists(ref_path):
                continue
            if not has_reference:
                user_content.append({
                    "type": "text",
                    "text": (
                        "\n## Reference images (class labels attached)\n"
                        "Use the following reference crops to inform LINE STYLE / ARROWHEAD / NODE ICON recognition for the main diagram. "
                        "Do NOT count them as elements of the target process; they are ONLY visual disambiguation aids:\n"
                    )
                })
                has_reference = True
            label = REFERENCE_LABELS.get(ref_file, os.path.splitext(ref_file)[0])
            user_content.append({
                "type": "text",
                "text": f"Reference class: {label}"
            })
            mime_ref = detect_image_mime(ref_path)
            b64_ref = image_to_base64(ref_path)
            print(f"加载参考图片: {ref_path} ({label})")
            user_content.append({
                "type": "image_url",
                "image_url": {"url": f"data:{mime_ref};base64,{b64_ref}"}
            })

        if not has_reference:
            print("未找到任何参考图片，将仅使用主图进行识别。")
    else:
        user_content.insert(2, {
            "type": "text",
            "text": (
                "\n## Reference images and captions\n"
                "[ABLATION: visual_reference=false] No reference images / reference captions will be attached. "
                "Structured output schema, temperature and other parameters remain identical to the full configuration.\n"
            )
        })

    return user_content


def _parse_and_normalize(raw_text):
    if raw_text is None:
        raise ExtractionConflictError("Empty model response.")
    try:
        obj = json.loads(raw_text)
    except json.JSONDecodeError as e:
        raise ExtractionConflictError(f"Extraction output is not valid JSON: {e}")
    return normalize_connection_refs(obj)


def run_extraction(
    image_path,
    reference_dir,
    current_dir,
    name_no_ext,
    call_model_func,
    ablation_config=None,
):
    print("\n--- 步骤 1: 元素提取 ---")

    try:
        width, height = get_image_size(image_path)
        mime = detect_image_mime(image_path)
        system_prompt = _build_extraction_system_prompt(ablation_config)
    except (FileNotFoundError, ValueError, ExtractionConflictError) as e:
        print(f"构建提取消息失败（知识/schema/图片缺失）: {e}")
        return None, str(e)

    user_content = _build_extraction_user_message(image_path, reference_dir, width, height, mime, ablation_config)

    messages_extraction = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_content}
    ]

    initial_raw, _ = call_model_func(messages_extraction)
    if not initial_raw:
        print("步骤 1 调用模型失败。")
        return None, "模型调用返回空"

    step1_out_path = save_result(
        current_dir, f"{name_no_ext}_step1_extraction.json", initial_raw
    )
    print(f"步骤 1 提取结果已保存至: {step1_out_path}")

    try:
        normalized = _parse_and_normalize(initial_raw)
    except ExtractionConflictError as e:
        conflict_path = save_json(
            current_dir, None, step1_out_path.replace(".json", "_conflicts.json"),
            {"conflicts": str(e).splitlines(), "raw": initial_raw}
        )
        print(f"步骤 1 冲突/无效 JSON，详情: {conflict_path}")
        return initial_raw, str(e)

    normalized_path = save_json(
        current_dir, None, step1_out_path.replace(".json", "_normalized.json"),
        normalized
    )
    print(f"步骤 1 JSON 已规范化: {normalized_path}")

    return json.dumps(normalized, ensure_ascii=False, indent=2), None

