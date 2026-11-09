import os
import json


OUTPUT_SUBDIR = "Gemini"
IMAGE_DIR = os.path.join("data", "image")
RENDER_SUBDIR = "render"
CROP_SUBDIR = "crops"

SRC_DIR = os.path.dirname(os.path.abspath(__file__))
BASE_DIR = os.path.dirname(SRC_DIR)
CONFIG_DIR = os.path.join(BASE_DIR, "config")
PROMPT_DIR = os.path.join(CONFIG_DIR, "prompts")
ABLATION_DIR = os.path.join(CONFIG_DIR, "ablation")

SCHEMA_DIR = os.path.join(BASE_DIR, "schemas")
ELEMENTS_SCHEMA_FILE = os.path.join(SCHEMA_DIR, "elements.schema.json")

KNOWLEDGE_FILE = os.path.join(CONFIG_DIR, "knowledge.txt")

RULES_FILE = os.path.join(CONFIG_DIR, "rules.json")

EXTRACT_AGENT_PROMPT_FILE = os.path.join(PROMPT_DIR, "extract agent prompt.json")
GENERATE_AGENT_PROMPT_FILE = os.path.join(PROMPT_DIR, "generate agent prompt.json")
REVIEW_AGENT_PROMPT_FILE = os.path.join(PROMPT_DIR, "review agent prompt.json")
XML_CORRECT_AGENT_PROMPT_FILE = os.path.join(PROMPT_DIR, "xml_correct_agent_prompt.json")
MULTIAGENT_MODIFY_AGENT_PROMPT_FILE = os.path.join(PROMPT_DIR, "multiagent_modify_agent_prompt.json")

DEPRECATED_PROMPT_EXTRACTION_FILE = os.path.join(PROMPT_DIR, "prompt09.json")
DEPRECATED_PROMPT_GENERATION_FILE = os.path.join(PROMPT_DIR, "prompt0.json")

IMAGE_DIR_PATH = os.path.join(BASE_DIR, IMAGE_DIR)
OUTPUT_DIR_PATH = os.path.join(BASE_DIR, OUTPUT_SUBDIR)

DEFAULT_IMAGE_NAME = "fc04.png"

REFERENCE_FILES = [
    "association.png",
    "data_object_reference.png",
    "data_store_reference.png",
    "end_event.png",
    "exclusive_gateway.png",
    "message_flow.png",
    "message_start_event.png",
    "parallel_gateway.png",
    "sequence_flow.png",
    "start_event.png",
    "time_start_event.png",
]

REFERENCE_LABELS = {
    "association.png": "Association (dotted/dash-dotted, open arrowhead when directed)",
    "data_object_reference.png": "DataObject visual shape",
    "data_store_reference.png": "DataStore visual shape (cylinder)",
    "end_event.png": "End Event (bold single circle, None type)",
    "exclusive_gateway.png": "Exclusive Gateway (diamond with X marker)",
    "message_flow.png": "MessageFlow (dashed line, open arrowhead)",
    "message_start_event.png": "Message Start Event (circle with envelope icon, dashed MessageFlow origin hint)",
    "parallel_gateway.png": "Parallel Gateway (diamond with + marker)",
    "sequence_flow.png": "SequenceFlow (solid line, filled arrowhead)",
    "start_event.png": "Start Event (thin single circle, None type)",
    "time_start_event.png": "Time Start Event (circle with clock/timer icon)",
}

API_URL = "https://api.openlux.ai/v1/chat/completions"
API_KEY_ENV_VAR = "REMA_API_KEY"

DEFAULT_MODEL = os.environ.get("REMA_MODEL", "gemini-3-pro-preview")
DEFAULT_TEMPERATURE = 1
DEFAULT_MAX_RETRIES = 3
DEFAULT_TIMEOUT = 1500

REVIEW_AND_EDIT_ROUNDS = 3

PIPELINE_STATUS_DONE = "done"
PIPELINE_STATUS_VALIDATION_FAILED = "validation_failed"
PIPELINE_STATUS_INSUFFICIENT_EVIDENCE = "insufficient_evidence"
PIPELINE_STATUS_FAILED = "failed"


DEFAULT_ABLATION = {
    "visual_reference": True,
    "knowledge_injection": True,
    "review_modify": True,
    "external_xml_checking": True,
}

ABLATION_PRESETS = {
    "rema_full": {
        "visual_reference": True,
        "knowledge_injection": True,
        "review_modify": True,
        "external_xml_checking": True,
    },
    "no_visual_reference": {
        "visual_reference": False,
        "knowledge_injection": True,
        "review_modify": True,
        "external_xml_checking": True,
    },
    "no_knowledge_injection": {
        "visual_reference": True,
        "knowledge_injection": False,
        "review_modify": True,
        "external_xml_checking": True,
    },
    "no_review_modify": {
        "visual_reference": True,
        "knowledge_injection": True,
        "review_modify": False,
        "external_xml_checking": True,
    },
    "no_external_xml_checking": {
        "visual_reference": True,
        "knowledge_injection": True,
        "review_modify": True,
        "external_xml_checking": False,
    },
    "no_deterministic_sync": {
        "visual_reference": True,
        "knowledge_injection": True,
        "review_modify": True,
        "external_xml_checking": True,
        "deterministic_sync": False,
    },
    "no_inventory_check": {
        "visual_reference": True, "knowledge_injection": True, "review_modify": True,
        "external_xml_checking": True, "inventory_check": False,
    },
    "no_supporting_pass": {
        "visual_reference": True, "knowledge_injection": True, "review_modify": True,
        "external_xml_checking": True, "supporting_pass": False,
    },
    "no_edge_completion": {
        "visual_reference": True, "knowledge_injection": True, "review_modify": True,
        "external_xml_checking": True, "edge_completion": False,
    },
    "no_formal_validation": {
        "visual_reference": True, "knowledge_injection": True, "review_modify": True,
        "external_xml_checking": True, "formal_validation": False,
    },
    "no_render_compare": {
        "visual_reference": True, "knowledge_injection": True, "review_modify": True,
        "external_xml_checking": True, "render_compare": False,
    },
    "review_without_tools": {
        "visual_reference": True, "knowledge_injection": True, "review_modify": True,
        "external_xml_checking": True, "review_tools": False,
    },
    "no_termination_policy": {
        "visual_reference": True, "knowledge_injection": True, "review_modify": True,
        "external_xml_checking": True, "end_event_policy": "faithful",
    },
}

REQUIRED_ABLATION_KEYS = (
    "visual_reference",
    "knowledge_injection",
    "review_modify",
    "external_xml_checking",
)

OPTIONAL_ABLATION_KEYS = {
    "deterministic_sync": True,
    "sync_semantic": True,
    "inventory_check": True,
    "supporting_pass": True,
    "edge_completion": True,
    "formal_validation": True,
    "render_compare": True,
    "review_tools": True,
}

OPTIONAL_STR_ABLATION_KEYS = {
    "end_event_policy": "normalized",
}
END_EVENT_POLICIES = ("faithful", "normalized", "interactive")

OPTIONAL_INT_ABLATION_KEYS = {
    "inventory_grid": 2,
    "max_tool_calls": 8,
    "render_rounds": 2,
    "edge_candidates": 3,
}
ALL_ABLATION_KEYS = (REQUIRED_ABLATION_KEYS + tuple(OPTIONAL_ABLATION_KEYS)
                     + tuple(OPTIONAL_STR_ABLATION_KEYS)
                     + tuple(OPTIONAL_INT_ABLATION_KEYS))


def _coerce_bool(val):
    if isinstance(val, bool):
        return val
    if isinstance(val, (int, float)):
        return bool(val)
    if isinstance(val, str):
        if val.lower() in {"1", "true", "yes", "on"}:
            return True
        if val.lower() in {"0", "false", "no", "off"}:
            return False
    raise ValueError(f"Cannot coerce ablation value to bool: {val!r}")


def _normalize_ablation(cfg, source="<unknown>"):
    if cfg is None:
        cfg = dict(DEFAULT_ABLATION)
    if not isinstance(cfg, dict):
        raise ValueError(f"Ablation config must be a dict, got {type(cfg).__name__} from {source}")
    missing = [k for k in REQUIRED_ABLATION_KEYS if k not in cfg]
    if missing:
        raise ValueError(f"Ablation config missing keys from {source}: {missing}")
    normalized = {}
    for k in REQUIRED_ABLATION_KEYS:
        try:
            normalized[k] = _coerce_bool(cfg[k])
        except Exception as e:
            raise ValueError(f"Ablation key {k!r} invalid from {source}: {e}")
    for k, default in OPTIONAL_ABLATION_KEYS.items():
        if k in cfg:
            try:
                normalized[k] = _coerce_bool(cfg[k])
            except Exception as e:
                raise ValueError(f"Ablation key {k!r} invalid from {source}: {e}")
        else:
            normalized[k] = default
    for k, default in OPTIONAL_STR_ABLATION_KEYS.items():
        val = cfg.get(k, default)
        if val not in END_EVENT_POLICIES:
            raise ValueError(
                f"Ablation key {k!r}={val!r} invalid from {source}; "
                f"must be one of {END_EVENT_POLICIES}")
        normalized[k] = val
    for k, default in OPTIONAL_INT_ABLATION_KEYS.items():
        val = cfg.get(k, default)
        try:
            ival = int(val)
        except Exception as e:
            raise ValueError(f"Ablation key {k!r} invalid from {source}: {e}")
        normalized[k] = max(0, ival)
    return normalized


def load_ablation_from_file(file_path):
    if not os.path.exists(file_path):
        raise FileNotFoundError(f"Ablation config file not found: {file_path}")
    with open(file_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    if isinstance(data, dict) and "ablation" in data and isinstance(data["ablation"], dict):
        cfg = data["ablation"]
        src = f"json file {os.path.basename(file_path)} [ablation block]"
    else:
        cfg = data
        src = f"json file {os.path.basename(file_path)} [top-level]"
    return _normalize_ablation(cfg, source=src)


def get_preset_ablation(name):
    if name not in ABLATION_PRESETS:
        raise KeyError(f"Unknown ablation preset: {name!r}. Available: {sorted(ABLATION_PRESETS)}")
    return _normalize_ablation(ABLATION_PRESETS[name], source=f"preset:{name}")


def get_ablation(preset_name=None, config_file=None):
    if config_file:
        cfg = load_ablation_from_file(config_file)
    elif preset_name:
        cfg = get_preset_ablation(preset_name)
    else:
        cfg = dict(DEFAULT_ABLATION)

    env_overrides = {}
    for k in ALL_ABLATION_KEYS:
        env_name = "ABLATION_" + k.upper()
        if env_name in os.environ:
            raw = os.environ[env_name]
            if k in OPTIONAL_STR_ABLATION_KEYS:
                if raw not in END_EVENT_POLICIES:
                    raise ValueError(
                        f"Environment {env_name}={raw!r} invalid: must be one of {END_EVENT_POLICIES}")
                env_overrides[k] = raw
                continue
            if k in OPTIONAL_INT_ABLATION_KEYS:
                try:
                    env_overrides[k] = int(raw)
                except Exception as e:
                    raise ValueError(f"Environment {env_name}={raw!r} invalid: {e}")
                continue
            try:
                env_overrides[k] = _coerce_bool(raw)
            except Exception as e:
                raise ValueError(f"Environment {env_name}={raw!r} invalid: {e}")
    if env_overrides:
        cfg.update(env_overrides)

    return _normalize_ablation(cfg, source="get_ablation resolved")


def ablation_config_filename(preset_or_name):
    if not isinstance(preset_or_name, str):
        raise ValueError("ablation config name must be string")
    return f"ablation_{preset_or_name}.json"


def write_preset_files(target_dir=ABLATION_DIR, names=None):
    os.makedirs(target_dir, exist_ok=True)
    written = {}
    for name, cfg in ABLATION_PRESETS.items():
        if names is not None and name not in names:
            continue
        path = os.path.join(target_dir, ablation_config_filename(name))
        payload = {
            "ablation_name": name,
            "ablation": _normalize_ablation(cfg, source=f"preset:{name}"),
            "model_version": DEFAULT_MODEL,
            "temperature": DEFAULT_TEMPERATURE,
            "notes": (
                "Generated ablation preset for ReMA. Do NOT modify backbone/temperature/eval procedure across presets. "
                "Toggle via get_ablation(preset_name=...) or ABLATION_* env or JSON file."
            ),
        }
        with open(path, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
        written[name] = path
    return written


def save_ablation_log(output_dir, base_name, ablation_config, extra=None):
    if not ablation_config:
        raise ValueError("ablation_config must be provided for traceable logs.")
    normalized = _normalize_ablation(ablation_config, source="save_ablation_log")
    record = {
        "base_name": base_name,
        "ablation": normalized,
        "model_version": DEFAULT_MODEL,
        "temperature": DEFAULT_TEMPERATURE,
        "max_retries": DEFAULT_MAX_RETRIES,
        "timeout_seconds": DEFAULT_TIMEOUT,
        "review_and_edit_rounds": REVIEW_AND_EDIT_ROUNDS,
    }
    if extra:
        if not isinstance(extra, dict):
            raise ValueError("save_ablation_log extra must be a dict if provided")
        record.update(extra)
    import file_utils
    return file_utils.save_json(output_dir, OUTPUT_SUBDIR, f"{base_name}_ablation.json", record)


def compute_valid_bpmn_file_rate(bpmn_paths):
    import xml.etree.ElementTree as ET

    if not bpmn_paths:
        return {"total": 0, "valid": 0, "rate": 0.0, "details": {}}
    BPMN_NS = "http://www.omg.org/spec/BPMN/20100524/MODEL"
    total = 0
    valid = 0
    details = {}
    for p in bpmn_paths:
        total += 1
        ok = False
        reason = None
        try:
            if not os.path.exists(p):
                reason = "file_not_found"
            else:
                tree = ET.parse(p)
                root = tree.getroot()
                tag_ok = False
                if "definitions" in root.tag.lower():
                    tag_ok = True
                if not tag_ok:
                    for _elem in root.iter():
                        tag = _elem.tag
                        if isinstance(tag, str) and tag.startswith("{%s}" % BPMN_NS):
                            tag_ok = True
                            break
                ok = tag_ok
                if not ok:
                    reason = "no_bpmn_root_or_elements"
        except Exception as e:
            reason = f"parse_error:{type(e).__name__}:{str(e)[:80]}"
        if ok:
            valid += 1
        details[p] = {"valid": ok, "reason": reason}
    return {
        "total": total,
        "valid": valid,
        "rate": (valid / total) if total else 0.0,
        "details": details,
    }


def _ensure_preset_files():
    missing = [name for name in ABLATION_PRESETS
               if not os.path.exists(os.path.join(ABLATION_DIR, ablation_config_filename(name)))]
    if missing:
        try:
            write_preset_files(ABLATION_DIR, names=missing)
        except Exception:
            pass


_ensure_preset_files()

