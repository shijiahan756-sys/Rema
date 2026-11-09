import os
import json
import base64

from config import OUTPUT_SUBDIR


def image_to_base64(image_path):
    with open(image_path, "rb") as image_file:
        encoded_string = base64.b64encode(image_file.read()).decode("utf-8")
    return encoded_string


def detect_image_mime(image_path):
    ext = os.path.splitext(image_path)[1].lower()
    if ext in (".jpg", ".jpeg"):
        return "image/jpeg"
    if ext == ".png":
        return "image/png"
    try:
        import imghdr
        guessed = imghdr.what(image_path)
    except Exception:
        guessed = None
    if guessed == "jpeg":
        return "image/jpeg"
    if guessed == "png":
        return "image/png"
    return "application/octet-stream"


def get_image_size(image_path):
    ext = os.path.splitext(image_path)[1].lower()
    if ext == ".png":
        with open(image_path, "rb") as f:
            header = f.read(24)
            if len(header) < 24 or header[:8] != b"\x89PNG\r\n\x1a\n":
                raise ValueError("Invalid PNG header")
            import struct
            w, h = struct.unpack(">II", header[16:24])
            return int(w), int(h)
    if ext in (".jpg", ".jpeg"):
        with open(image_path, "rb") as f:
            data = f.read()
        if len(data) < 2 or data[0] != 0xFF or data[1] != 0xD8:
            raise ValueError("Invalid JPEG header")
        i = 2
        while i < len(data):
            if data[i] != 0xFF:
                raise ValueError("Invalid JPEG marker")
            marker = data[i + 1]
            if marker in (0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF):
                h = (data[i + 5] << 8) | data[i + 6]
                w = (data[i + 7] << 8) | data[i + 8]
                return int(w), int(h)
            else:
                seglen = (data[i + 2] << 8) | data[i + 3]
                i += 2 + seglen
        raise ValueError("JPEG SOF marker not found")
    try:
        from PIL import Image
    except Exception:
        raise ValueError(f"Unsupported image extension for size reading: {ext}")
    with Image.open(image_path) as img:
        return img.size


def load_plain_text(file_path, label):
    if not os.path.exists(file_path):
        raise FileNotFoundError(f"{label} file not found: {file_path}")
    with open(file_path, "r", encoding="utf-8") as f:
        data = f.read()
    if not data.strip():
        raise ValueError(f"{label} file is empty: {file_path}")
    return data


def load_prompt_text(prompt_file_path):
    with open(prompt_file_path, "r", encoding="utf-8") as f:
        return json.load(f).get("text", "")


def load_json_file(file_path):
    with open(file_path, "r", encoding="utf-8") as f:
        return json.load(f)


def load_schema(file_path):
    return load_json_file(file_path)


def save_text(output_dir, subdir, filename, content):
    save_dir = os.path.join(output_dir, subdir) if subdir else output_dir
    os.makedirs(save_dir, exist_ok=True)
    out_path = os.path.join(save_dir, filename)
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(content)
    return out_path


def save_result(output_dir, filename, content):
    return save_text(output_dir, OUTPUT_SUBDIR, filename, content)


def save_json(output_dir, subdir, filename, obj):
    return save_text(output_dir, subdir, filename, json.dumps(obj, ensure_ascii=False, indent=2))


def ensure_output_dirs(base_dir):
    from config import IMAGE_DIR, OUTPUT_SUBDIR, SCHEMA_DIR
    os.makedirs(os.path.join(base_dir, IMAGE_DIR), exist_ok=True)
    os.makedirs(os.path.join(base_dir, OUTPUT_SUBDIR), exist_ok=True)
    os.makedirs(SCHEMA_DIR, exist_ok=True)
