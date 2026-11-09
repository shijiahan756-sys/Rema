import os
import sys
import argparse
import json
import glob

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "src"))

from model_client import call_model
from pipeline_blind import run_pipeline_blind, EXPERIMENT_OUTPUT_SUBDIR
from config import get_ablation, BASE_DIR, IMAGE_DIR, REFERENCE_FILES, ABLATION_PRESETS


BATCH_IMAGE_EXTS = (".jpg", ".jpeg", ".png")


def _is_reference(name):
    return name in REFERENCE_FILES


def _discover_all_images(current_dir):
    img_dir = os.path.join(current_dir, IMAGE_DIR)
    if not os.path.isdir(img_dir):
        return []
    candidates = []
    for ext in BATCH_IMAGE_EXTS:
        candidates.extend(glob.glob(os.path.join(img_dir, "*" + ext)))
    seen = set()
    ordered = []
    for p in sorted(candidates):
        name = os.path.basename(p)
        if _is_reference(name):
            continue
        if name in seen:
            continue
        seen.add(name)
        ordered.append(name)
    return ordered


def _load_image_list_file(path):
    if not os.path.exists(path):
        raise FileNotFoundError(f"--image-list file not found: {path}")
    with open(path, "r", encoding="utf-8") as f:
        raw = f.read()
    try:
        data = json.loads(raw)
        if isinstance(data, list):
            items = [str(x) for x in data if str(x).strip()]
        else:
            raise ValueError("JSON image list must be a top-level array of image names.")
    except json.JSONDecodeError:
        items = [line.strip() for line in raw.splitlines() if line.strip() and not line.strip().startswith("#")]
    cleaned = []
    for it in items:
        if os.sep in it or "/" in it:
            it = os.path.basename(it)
        cleaned.append(it)
    return cleaned


def _parse_args():
    parser = argparse.ArgumentParser(
        description="ReMA BLIND MODIFY EXPERIMENT pipeline (single or batch). "
                    "Review Agent sees the original diagram image; "
                    "Modify Agent operates BLINDLY (receives only Review Report + element JSON, NO IMAGE). "
                    "All existing ablation switches (visual_reference, knowledge_injection, review_modify, "
                    "external_xml_checking) still apply via CLI / env / JSON preset."
    )
    parser.add_argument("--image-name", default=None,
                        help="[Single] Input image name under IMAGE_DIR (default: config.DEFAULT_IMAGE_NAME). "
                             "Ignored when any batch option is set.")
    parser.add_argument("--images", nargs="+", default=None,
                        help="[Batch] Space-separated image names under IMAGE_DIR, e.g. --images com029.jpg fc01.png")
    parser.add_argument("--image-list", default=None, metavar="FILE",
                        help="[Batch] Path to a text/JSON file listing image names (one per line or JSON array). "
                             "Lines starting with # are ignored.")
    parser.add_argument("--all", action="store_true",
                        help="[Batch] Run on every business diagram under IMAGE_DIR, excluding legend/reference PNGs.")
    parser.add_argument("--ablation-preset", default=None,
                        choices=sorted(ABLATION_PRESETS),
                        help=f"Load one of {len(ABLATION_PRESETS)} bundled ablation presets "
                             f"(applied to all batch items). List driven by config.ABLATION_PRESETS.")
    parser.add_argument("--ablation-config", default=None,
                        help="Path to ablation JSON file (top-level 4 bool keys or {\"ablation\":{...}}).")
    parser.add_argument("--batch-summary", default=None, metavar="FILE",
                        help="Write a batch summary JSON to the given path (default: auto under experiment output dir).")
    parser.add_argument("--continue-on-error", action="store_true", default=True,
                        help="Batch mode: skip failed items and continue (default on). Use --no-continue-on-error to stop on first failure.")
    parser.add_argument("--no-continue-on-error", dest="continue_on_error", action="store_false")
    return parser.parse_args()


def _resolve_batch_images(args, current_dir):
    if args.all:
        names = _discover_all_images(current_dir)
        if not names:
            raise ValueError(f"--all set but no business images found under {IMAGE_DIR}/ (reference files excluded).")
        return names, "all"
    if args.image_list:
        names = _load_image_list_file(args.image_list)
        if not names:
            raise ValueError(f"--image-list produced no image names: {args.image_list}")
        return names, f"list@{args.image_list}"
    if args.images:
        return list(args.images), "explicit"
    if args.image_name is not None:
        return [args.image_name], "single"
    return None, "default_single"


def _write_batch_summary(current_dir, ablation_config, mode_label, per_image_results, out_path=None):
    total = len(per_image_results)
    passed = sum(1 for r in per_image_results if r.get("status") == "done")
    validation_failed = sum(1 for r in per_image_results if r.get("status") == "validation_failed")
    evidence = sum(1 for r in per_image_results if r.get("status") == "insufficient_evidence")
    failed = sum(1 for r in per_image_results if r.get("status") == "failed")
    summary = {
        "experiment": "blind_modify",
        "mode": "batch" if total > 1 else "single",
        "batch_mode": mode_label,
        "output_subdir": EXPERIMENT_OUTPUT_SUBDIR,
        "total": total,
        "counts": {
            "done": passed,
            "validation_failed": validation_failed,
            "insufficient_evidence": evidence,
            "failed": failed,
        },
        "ablation_config": ablation_config,
        "results": per_image_results,
    }
    if out_path is None:
        import time
        stamp = time.strftime("%Y%m%d_%H%M%S")
        out_path = os.path.join(current_dir, EXPERIMENT_OUTPUT_SUBDIR, f"batch_summary_{stamp}.json")
    os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)
    return out_path, summary


def main():
    args = _parse_args()
    current_dir = BASE_DIR

    ablation_config = get_ablation(
        preset_name=args.ablation_preset,
        config_file=args.ablation_config,
    )

    names, mode_label = _resolve_batch_images(args, current_dir)
    if names is None:
        names = [None]

    print("=" * 60)
    print(f"ReMA BLIND MODIFY 实验 执行模式: {'batch' if len(names) > 1 else 'single'} | "
          f"batch_source={mode_label} | items={len(names)}")
    print(f"  images: {names if len(names) <= 12 else names[:12] + ['...(+' + str(len(names)-12) + ' more)']}")
    print(f"  ablation: {ablation_config}")
    print(f"  experiment_output_subdir: {EXPERIMENT_OUTPUT_SUBDIR}")
    print("=" * 60)

    per_image_results = []
    exit_code = 0

    for idx, img_name in enumerate(names, start=1):
        print()
        print(f"[{idx}/{len(names)}] ====== Blind Modify Processing {img_name or '<default>'} ======")
        run_kwargs = {
            "call_model_func": call_model,
            "ablation_config": ablation_config,
        }
        if img_name is not None:
            run_kwargs["image_name"] = img_name
        try:
            result = run_pipeline_blind(**run_kwargs)
            rec = {
                "index": idx,
                "image_name": img_name,
                "status": result.get("status"),
                "message": result.get("message"),
                "output_path": result.get("output_path"),
                "ablation_log_path": result.get("ablation_log_path"),
            }
            if "valid_bpmn_file_rate_current" in result:
                rate_info = result["valid_bpmn_file_rate_current"]
                rec["valid_bpmn_file_rate"] = {
                    "total": rate_info.get("total"),
                    "valid": rate_info.get("valid"),
                    "rate": rate_info.get("rate"),
                }
            per_image_results.append(rec)
            print(f"  -> {rec['status']}")
        except Exception as e:
            rec = {
                "index": idx,
                "image_name": img_name,
                "status": "failed",
                "message": f"Unhandled exception: {type(e).__name__}: {e}",
            }
            per_image_results.append(rec)
            print(f"  !-> EXCEPTION {type(e).__name__}: {e}")
            if not args.continue_on_error:
                exit_code = 2
                break
            exit_code = 1

    summary_path, summary = _write_batch_summary(
        current_dir, ablation_config, mode_label, per_image_results,
        out_path=args.batch_summary,
    )
    print()
    print("=" * 60)
    print("BLIND MODIFY 批量执行汇总 (summary written to):", summary_path)
    print(f"  total={summary['total']} "
          f"done={summary['counts']['done']} "
          f"validation_failed={summary['counts']['validation_failed']} "
          f"insufficient_evidence={summary['counts']['insufficient_evidence']} "
          f"failed={summary['counts']['failed']}")
    print("=" * 60)

    sys.exit(exit_code)


if __name__ == "__main__":
    main()
