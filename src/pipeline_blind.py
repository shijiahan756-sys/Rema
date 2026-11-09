import os

from config import (
    BASE_DIR,
    RULES_FILE,
    IMAGE_DIR,
    DEFAULT_IMAGE_NAME,
    PIPELINE_STATUS_DONE,
    PIPELINE_STATUS_VALIDATION_FAILED,
    PIPELINE_STATUS_INSUFFICIENT_EVIDENCE,
    PIPELINE_STATUS_FAILED,
    get_ablation,
    save_ablation_log,
    OUTPUT_SUBDIR as DEFAULT_OUTPUT_SUBDIR,
)
from file_utils import ensure_output_dirs, save_text, save_json
from extraction import run_extraction
import multiagent_blind as multiagent
from generation import run_generation
from validation import run_validation, VALIDATION_OK, VALIDATION_FAILED
import bpmn_sync
import inventory
import edge_completion
import termination
import render_compare

EXPERIMENT_OUTPUT_SUBDIR = DEFAULT_OUTPUT_SUBDIR + "_blind_modify"


def _short(status):
    return {
        multiagent.REVIEW_STATUS_PASSED: "passed",
        multiagent.REVIEW_STATUS_REVISION: "needs_revision",
        multiagent.REVIEW_STATUS_EVIDENCE: "needs_evidence",
        multiagent.REVIEW_STATUS_FAILED: "failed",
    }.get(status, status)


def run_pipeline_blind(
    call_model_func,
    image_name=DEFAULT_IMAGE_NAME,
    current_dir=BASE_DIR,
    ablation_config=None,
):
    print("=" * 60)
    print("ReMA BLIND MODIFY 实验 Pipeline 启动")
    print("  实验特性: Review Agent 可见原图；Modify Agent 盲改（仅 Review Report + JSON，无原图）")
    print("=" * 60)

    ensure_output_dirs(current_dir)

    if ablation_config is None:
        ablation_config = get_ablation()

    blind_ablation = dict(ablation_config)
    blind_ablation["experiment"] = "blind_modify"
    blind_ablation["modify_agent_sees_image"] = False
    blind_ablation["review_agent_sees_image"] = True

    image_path = os.path.join(current_dir, IMAGE_DIR, image_name)
    reference_dir = os.path.join(current_dir, IMAGE_DIR)

    if not os.path.exists(image_path):
        print(f"警告: 路径 {image_path} 未找到。")
        return {"status": PIPELINE_STATUS_FAILED, "message": f"Image not found: {image_path}"}

    base_name = os.path.basename(image_path)
    name_no_ext = os.path.splitext(base_name)[0]
    print(f"正在处理图片: {image_path}")
    print(f"[Ablation + Experiment config] {blind_ablation}")

    ablation_log_path = save_ablation_log(
        current_dir,
        name_no_ext,
        blind_ablation,
        extra={
            "image_name": image_name,
            "image_path": image_path,
            "experiment_name": "blind_modify",
            "output_subdir": EXPERIMENT_OUTPUT_SUBDIR,
        },
    )
    print(f"Ablation + 实验配置日志已保存: {ablation_log_path}")

    initial_elements, extraction_error = run_extraction(
        image_path=image_path,
        reference_dir=reference_dir,
        current_dir=current_dir,
        name_no_ext=name_no_ext,
        call_model_func=call_model_func,
        ablation_config=ablation_config,
    )
    if extraction_error and not initial_elements:
        print("步骤 1 失败（知识/schema/模型调用）。")
        return {"status": PIPELINE_STATUS_FAILED, "message": extraction_error,
                "ablation_config": blind_ablation, "ablation_log_path": ablation_log_path}


    rc1_errors, comp_info = [], None
    try:
        initial_elements, rc1_errors, comp_info = inventory.run_completeness_pass(
            initial_elements, image_path, call_model_func,
            ablation=blind_ablation, output_dir=current_dir, base_name=name_no_ext)
    except Exception as e:
        print(f"[completeness] 失败，按跳过处理: {e}")

    rc2_errors, edge_info = [], None
    try:
        initial_elements, rc2_errors, edge_info = edge_completion.run_edge_completion(
            initial_elements, image_path, call_model_func,
            ablation=blind_ablation, output_dir=current_dir, base_name=name_no_ext)
    except Exception as e:
        print(f"[edge-completion] 失败，按跳过处理: {e}")

    review_needs_revision = False
    review_findings_count = 0
    if blind_ablation.get("review_modify", True):
        final_elements = multiagent.review_and_edit(
            initial_elements=initial_elements,
            rules_file=RULES_FILE,
            call_model_func=call_model_func,
            output_dir=current_dir,
            base_name=name_no_ext,
            image_path=image_path,
            inventory_errors=rc1_errors,
            edge_errors=rc2_errors,
            ablation_config=blind_ablation,
        )
        review_status = multiagent.get_last_review_status()
        review_findings_count = len(multiagent.get_last_findings() or [])
        print(f"步骤 2 审查状态 (Blind Modify): {_short(review_status)} | "
              f"Review Agent 工具层 findings={review_findings_count}")

        if review_status == multiagent.REVIEW_STATUS_FAILED:
            msg = "步骤 2 发生程序故障（配置/模型/报告格式异常）；不得当作证据不足或 XML 错误继续。"
            print(msg)
            return {"status": PIPELINE_STATUS_FAILED, "message": msg,
                    "ablation_config": blind_ablation, "ablation_log_path": ablation_log_path}
        if review_status is None and not final_elements:
            msg = "步骤 2 未返回可用结果。"
            print(msg)
            return {"status": PIPELINE_STATUS_FAILED, "message": msg,
                    "ablation_config": blind_ablation, "ablation_log_path": ablation_log_path}

        if review_status == multiagent.REVIEW_STATUS_REVISION:
            review_needs_revision = True
            print("步骤 2 达到最大轮次后仍存在 errors（REVISION）：将继续生成 XML 并保存，"
                  "但最终文件加 _needs_revision 后缀，且不得宣称通过。"
                  "（Blind Modify 实验：仅 Review Agent 曾可见原图）")

        evidence_insufficient = (review_status == multiagent.REVIEW_STATUS_EVIDENCE)
        if evidence_insufficient:
            print("元素 JSON 含需原图复核的 unverifiable 项（证据不足）：将继续生成但结果不得标记为已验证通过。")
    else:
        print("[Ablation: review_modify=false] Bypass Phase 2. Extract 输出直接送入 Convert/Generation。")
        final_elements = initial_elements
        review_status = "bypass"
        evidence_insufficient = False

    bpmn_xml = run_generation(
        final_elements=final_elements,
        call_model_func=call_model_func,
        base_name=name_no_ext,
        output_dir=current_dir,
        ablation_config=blind_ablation,
    )
    if not bpmn_xml:
        print("步骤 3 (生成) 失败。")
        return {"status": PIPELINE_STATUS_FAILED, "message": "Generation returned empty XML.",
                "ablation_config": blind_ablation, "ablation_log_path": ablation_log_path}

    term_policy = blind_ablation.get("end_event_policy", "normalized")
    try:
        bpmn_xml, term_report = termination.apply_termination_policy(
            bpmn_xml, final_elements, term_policy)
    except Exception as e:
        term_report = {"policy": term_policy, "error": str(e)}
        print(f"[termination] 失败，按跳过处理: {e}")

    if blind_ablation.get("external_xml_checking", True):
        val_status, validated_xml = run_validation(
            bpmn_xml=bpmn_xml,
            final_elements_json=final_elements,
            call_model_func=call_model_func,
        )
    else:
        print("[Ablation: external_xml_checking=false] Bypass Tag/Attribute/Namespace check and XML correction. initial BPMN as final.")
        validated_xml = bpmn_xml
        val_status = "bypass"

    final_sync = None
    if blind_ablation.get("deterministic_sync", True) and validated_xml:
        try:
            validated_xml, final_sync = bpmn_sync.synchronize(
                final_elements, validated_xml,
                add_semantic=blind_ablation.get("sync_semantic", True))
            print(f"[落盘前复核] {final_sync}")
        except Exception as e:
            print(f"[落盘前复核] 对齐失败，保留当前 XML：{e}")


    rc_info = None
    if blind_ablation.get("render_compare", True) and validated_xml:
        try:
            validated_xml, final_elements, rc_info = render_compare.run_render_compare(
                validated_xml, final_elements, image_path, call_model_func,
                ablation=blind_ablation, output_dir=current_dir, base_name=name_no_ext)
        except Exception as e:
            print(f"[render-compare] 核对失败，按跳过处理: {e}")
    name_suffix = "_needs_revision" if review_needs_revision else ""
    processed_filename = f"{name_no_ext}{name_suffix}_processed.bpmn"
    status_filename = f"{name_no_ext}{name_suffix}_status.json"
    rate_filename = f"{name_no_ext}{name_suffix}_valid_bpmn_rate.json"

    out_path = save_text(
        current_dir, EXPERIMENT_OUTPUT_SUBDIR, processed_filename, validated_xml
    )
    print(f"最终 XML 已保存: {out_path}")

    status_summary = {
        "experiment": "blind_modify",
        "ablation_config": blind_ablation,
        "ablation_log_path": ablation_log_path,
        "extraction_error": extraction_error,
        "review_status": _short(review_status),
        "review_needs_revision": review_needs_revision,
        "review_modify_enabled": blind_ablation.get("review_modify", True),
        "external_xml_checking_enabled": blind_ablation.get("external_xml_checking", True),
        "termination": {k: (term_report or {}).get(k)
                        for k in ("policy", "added_end_event", "needs_human_review",
                                  "documentation_added")},
        "needs_human_review": bool((term_report or {}).get("needs_human_review")),
        "completeness": {"rc1": len(rc1_errors),
                         "rescans": len(((comp_info or {}).get("rescans") or [])),
                         "merged": len(((comp_info or {}).get("merged") or []))},
        "edge_completion": {"added_flows": len(((edge_info or {}).get("added_flows") or [])),
                            "added_assoc": len(((edge_info or {}).get("added_assoc") or [])),
                            "dangling_after": (edge_info or {}).get("dangling_after")},
        "review_findings": review_findings_count,
        "render_compare": {"mode": (rc_info or {}).get("mode", "report_only"),
                           "rendered": bool((rc_info or {}).get("rendered")),
                           "rounds": len(((rc_info or {}).get("rounds") or [])),
                           "missing": len(((rc_info or {}).get("missing") or [])),
                           "extra": len(((rc_info or {}).get("extra") or [])),
                           "findings": len(((rc_info or {}).get("findings") or [])),
                           "skipped": (rc_info or {}).get("skipped")},
        "validation_status": val_status,
        "output_path": out_path,
        "output_filename_suffix": name_suffix or "(none)",
    }
    summary_path = save_json(
        current_dir, None, os.path.join(os.path.dirname(out_path), status_filename),
        status_summary,
    )
    print(f"状态摘要已保存: {summary_path}")

    from config import compute_valid_bpmn_file_rate
    single_rate = compute_valid_bpmn_file_rate([out_path])
    rate_summary_path = save_json(
        current_dir, None, os.path.join(os.path.dirname(out_path), rate_filename),
        {"ablation_config": blind_ablation,
         "experiment": "blind_modify",
         "review_needs_revision": review_needs_revision,
         "rate_for_current_file_only": single_rate},
    )
    print(f"Valid BPMN File Rate（当前样本）已保存: {rate_summary_path}")

    if blind_ablation.get("external_xml_checking", True) and val_status != VALIDATION_OK:
        msg = "XML Tag、Attribute、Namespaces 校验未通过，已保存但不宣称合规。"
        if review_needs_revision:
            msg += " （同时步骤 2 最终仍为 REVISION，文件已加 _needs_revision 后缀）"
        return {
            "status": PIPELINE_STATUS_VALIDATION_FAILED,
            "message": msg,
            "output_path": out_path,
            "ablation_config": blind_ablation,
            "ablation_log_path": ablation_log_path,
            "valid_bpmn_file_rate_current": single_rate,
            "review_needs_revision": review_needs_revision,
            "output_filename_suffix": name_suffix or "",
            "experiment": "blind_modify",
        }
    if review_needs_revision:
        return {
            "status": PIPELINE_STATUS_VALIDATION_FAILED,
            "message": ("步骤 2 达到最大轮次后仍存在确定性 errors（needs_revision），虽已继续生成并保存 XML，"
                        "但该结果不得视为已验证通过。文件已加 _needs_revision 后缀。"
                        "（Blind Modify 实验：仅 Review Agent 曾可见原图）"),
            "output_path": out_path,
            "ablation_config": blind_ablation,
            "ablation_log_path": ablation_log_path,
            "valid_bpmn_file_rate_current": single_rate,
            "review_needs_revision": True,
            "output_filename_suffix": name_suffix,
            "experiment": "blind_modify",
        }
    if evidence_insufficient:
        return {
            "status": PIPELINE_STATUS_INSUFFICIENT_EVIDENCE,
            "message": ("已生成 XML 且 Tag、Attribute、Namespaces 检查通过，但步骤 2 存在证据不足(unverifiable)项；"
                        "该结果不得视为已验证通过。"),
            "output_path": out_path,
            "ablation_config": blind_ablation,
            "ablation_log_path": ablation_log_path,
            "valid_bpmn_file_rate_current": single_rate,
            "experiment": "blind_modify",
        }
    if blind_ablation.get("external_xml_checking", True):
        print(f"\nBlind Modify 实验流程完成，Tag、Attribute、Namespaces 检查通过。")
    else:
        print(f"\nBlind Modify 实验流程完成（external_xml_checking 已绕过）。")
    print(f"最终结果已保存至: {out_path}")
    return {"status": PIPELINE_STATUS_DONE, "output_path": out_path,
            "ablation_config": blind_ablation,
            "ablation_log_path": ablation_log_path,
            "valid_bpmn_file_rate_current": single_rate,
            "experiment": "blind_modify"}
