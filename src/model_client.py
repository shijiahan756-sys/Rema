import os
import time
import requests

from config import (
    API_URL,
    API_KEY_ENV_VAR,
    DEFAULT_MODEL,
    DEFAULT_TEMPERATURE,
    DEFAULT_MAX_RETRIES,
    DEFAULT_TIMEOUT,
)


def _get_api_key():
    api_key = os.environ.get(API_KEY_ENV_VAR)
    if not api_key:
        raise EnvironmentError(
            f"环境变量 {API_KEY_ENV_VAR} 未设置。请设置 API 密钥后重试。"
        )
    return api_key


def _build_headers():
    api_key = _get_api_key()
    return {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }


def _clean_content(content):
    if isinstance(content, list):
        parts = []
        for p in content:
            if isinstance(p, str):
                parts.append(p)
            elif isinstance(p, dict):
                parts.append(p.get("text") or p.get("content") or "")
        content = "".join(parts)
    elif not isinstance(content, str):
        content = "" if content is None else str(content)
    content = content.strip()
    if content.startswith("```json"):
        content = content[7:]
    elif content.startswith("```xml"):
        content = content[6:]
    elif content.startswith("```"):
        content = content[3:]
    if content.endswith("```"):
        content = content[:-3]
    return content.strip()


def call_model(
    messages,
    model=DEFAULT_MODEL,
    max_retries=DEFAULT_MAX_RETRIES,
    temperature=DEFAULT_TEMPERATURE,
    timeout=DEFAULT_TIMEOUT,
):
    payload = {
        "model": model,
        "messages": messages,
    }
    if temperature is not None:
        payload["temperature"] = temperature

    headers = _build_headers()

    for attempt in range(max_retries):
        try:
            resp = requests.post(API_URL, headers=headers, json=payload, timeout=timeout)
            resp.raise_for_status()
            data = resp.json()
            content = data["choices"][0]["message"]["content"]
            usage = data.get("usage", {})
            content = _clean_content(content)
            return content, usage
        except (requests.exceptions.RequestException, ConnectionResetError) as e:
            print(f"调用模型失败 (尝试 {attempt + 1}/{max_retries}): {e}")
            if attempt < max_retries - 1:
                wait_time = (attempt + 1) * 2
                print(f"正在等待 {wait_time} 秒后重试...")
                time.sleep(wait_time)
            else:
                print("已达到最大重试次数。")
                return None, {}
        except Exception as e:
            print(f"发生未知错误: {e}")
            return None, {}
