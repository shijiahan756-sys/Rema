from config import GENERATE_AGENT_PROMPT_FILE, get_ablation, OUTPUT_SUBDIR
from file_utils import load_prompt_text, save_json
import bpmn_sync


def _build_generation_messages(final_elements, generation_system_prompt):
    return [
        {"role": "system", "content": generation_system_prompt},
        {
            "role": "user",
            "content": (
                "## Final elements JSON (ground truth)\n"
                f"{final_elements}\n\n"
                "## Output\n"
                "Emit the complete valid BPMN 2.0 XML. Include definitions / process / collaboration if needed / BPMNDiagram "
                "with shapes and edges for ALL listed elements. Preserve JSON coordinates as-is, only add intermediate waypoints "
                "when strictly needed for a sensible layout, never delete an element to avoid an error."
            )
        }
    ]


def run_generation(final_elements, call_model_func, base_name=None, output_dir=None,
                   ablation_config=None):
    print("\n--- 步骤 3: BPMN XML 生成 ---")

    try:
        generation_system_prompt = load_prompt_text(GENERATE_AGENT_PROMPT_FILE)
    except Exception as e:
        print(f"读取生成提示词失败: {e}")
        return None

    messages_generation = _build_generation_messages(
        final_elements, generation_system_prompt
    )

    bpmn_xml, _ = call_model_func(messages_generation)
    if not bpmn_xml:
        print("步骤 3 模型返回空。")
        return None

    ablation = ablation_config or get_ablation()
    if not ablation.get("deterministic_sync", True):
        print("[Ablation: deterministic_sync=false] 跳过 JSON→XML 确定性对齐。")
        return bpmn_xml

    try:
        bpmn_xml, report = bpmn_sync.synchronize(
            final_elements, bpmn_xml,
            add_semantic=ablation.get("sync_semantic", True))
    except Exception as e:
        print(f"[bpmn_sync 警告] 对齐失败，保留模型原始 XML：{e}")
        return bpmn_xml

    print("    " + str(report))
    cons = bpmn_sync.conservation_check(final_elements, bpmn_xml, report.id_map)
    missing = {k: v["missing_ids"] for k, v in cons.items()
               if isinstance(v, dict) and v.get("missing_ids")}
    if missing:
        print(f"    [bpmn_sync] 仍有未落地的 JSON 元素：{missing}")
    else:
        print("    [bpmn_sync] 元素守恒校验通过（JSON 中每个元素都能在 XML 中找到）")

    if output_dir and base_name:
        try:
            payload = report.as_dict()
            payload["conservation"] = cons
            payload["di"] = bpmn_sync.di_coverage(bpmn_xml)
            save_json(output_dir, OUTPUT_SUBDIR,
                      f"{base_name}_step3_sync_report.json", payload)
        except Exception as e:
            print(f"    [bpmn_sync] 报告落盘失败（不影响主流程）：{e}")

    return bpmn_xml
