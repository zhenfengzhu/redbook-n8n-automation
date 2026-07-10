from __future__ import annotations

import argparse
import base64
import hashlib
import hmac
import json
import os
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DIGEST = PROJECT_ROOT / "data" / "daily-digest" / "latest-digest.md"
DEFAULT_ENV_FILE = PROJECT_ROOT / "data" / "feishu" / "feishu.env"
DEFAULT_OUTPUT_DIR = PROJECT_ROOT / "data" / "feishu"
MAX_TEXT_LENGTH = 14000


for stream in (sys.stdout, sys.stderr):
    if hasattr(stream, "reconfigure"):
        stream.reconfigure(encoding="utf-8", errors="replace")


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def local_time_label() -> str:
    china_tz = timezone(timedelta(hours=8), name="Asia/Shanghai")
    return datetime.now(timezone.utc).astimezone(china_tz).strftime("%Y-%m-%d %H:%M:%S")


def resolve_workspace_path(path: str | Path) -> Path:
    candidate = Path(path)
    if not candidate.is_absolute():
        candidate = PROJECT_ROOT / candidate
    return candidate


def read_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def config_value(env_file_values: dict[str, str], key: str, default: str = "") -> str:
    return os.environ.get(key) or env_file_values.get(key, default)


def write_env_template(path: Path) -> None:
    lines = [
        "# Copy this file to feishu.env and fill real values. Do not commit real webhook URLs or secrets.",
        "FEISHU_WEBHOOK_URL=",
        "FEISHU_WEBHOOK_SECRET=",
        "FEISHU_KEYWORD=RedBook",
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")


def make_feishu_sign(timestamp: str, secret: str) -> str:
    key = f"{timestamp}\n{secret}".encode("utf-8")
    digest = hmac.new(key, b"", digestmod=hashlib.sha256).digest()
    return base64.b64encode(digest).decode("utf-8")


def build_message_text(digest_text: str, keyword: str) -> str:
    text = digest_text.strip()
    if keyword and keyword not in text:
        text = f"{keyword}\n\n{text}"
    if len(text) > MAX_TEXT_LENGTH:
        text = text[: MAX_TEXT_LENGTH - 40].rstrip() + "\n\n...已截断，请查看本地日报文件"
    return text


def build_payload(text: str, secret: str) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "msg_type": "text",
        "content": {
            "text": text,
        },
    }
    if secret:
        timestamp = str(int(time.time()))
        payload["timestamp"] = timestamp
        payload["sign"] = make_feishu_sign(timestamp, secret)
    return payload


def send_payload(webhook_url: str, payload: dict[str, Any], timeout: int) -> dict[str, Any]:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        webhook_url,
        data=body,
        headers={"Content-Type": "application/json; charset=utf-8"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            response_body = response.read().decode("utf-8", errors="replace")
            parsed = json.loads(response_body) if response_body else {}
            return {
                "http_status": response.status,
                "response": parsed,
                "ok": response.status == 200 and is_feishu_success(parsed),
            }
    except urllib.error.HTTPError as error:
        body_text = error.read().decode("utf-8", errors="replace")
        return {
            "http_status": error.code,
            "response": body_text,
            "ok": False,
        }
    except urllib.error.URLError as error:
        return {
            "http_status": None,
            "response": str(error.reason),
            "ok": False,
        }


def is_feishu_success(response: object) -> bool:
    if not isinstance(response, dict):
        return False
    code = response.get("code", response.get("StatusCode"))
    return code in (0, "0", None) and response.get("StatusMessage", "success") != "fail"


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def write_markdown(path: Path, summary: dict[str, Any]) -> None:
    lines = [
        "# Feishu Digest Send Summary",
        "",
        f"Generated at: {summary['generated_at']}",
        f"Local time: {summary['local_generated_at']}",
        f"Dry run: {summary['dry_run']}",
        f"Digest: `{summary['digest']}`",
        f"Webhook configured: {summary['webhook_configured']}",
        f"Secret configured: {summary['secret_configured']}",
        f"Message characters: {summary['message_characters']}",
        f"Result: {summary['result']}",
    ]
    if summary.get("http_status") is not None:
        lines.append(f"HTTP status: {summary['http_status']}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description="Send the latest RedBook daily digest to a Feishu custom bot webhook.")
    parser.add_argument("--digest", default=str(DEFAULT_DIGEST), help="Digest markdown path.")
    parser.add_argument("--env-file", default=str(DEFAULT_ENV_FILE), help="Feishu env file path.")
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR), help="Output directory for payload and summaries.")
    parser.add_argument("--write-env-template", action="store_true", help="Write feishu.env.example and exit.")
    parser.add_argument("--send", action="store_true", help="Actually send to Feishu. Default is dry-run.")
    parser.add_argument("--timeout", type=int, default=20, help="HTTP timeout in seconds.")
    args = parser.parse_args()

    env_file = resolve_workspace_path(args.env_file)
    output_dir = resolve_workspace_path(args.output_dir)
    if args.write_env_template:
        write_env_template(output_dir / "feishu.env.example")
        print(str(output_dir / "feishu.env.example"), flush=True)
        return

    digest_path = resolve_workspace_path(args.digest)
    if not digest_path.exists():
        raise RuntimeError(f"Digest file does not exist: {digest_path}")

    env_values = read_env_file(env_file)
    webhook_url = config_value(env_values, "FEISHU_WEBHOOK_URL")
    secret = config_value(env_values, "FEISHU_WEBHOOK_SECRET")
    keyword = config_value(env_values, "FEISHU_KEYWORD", "RedBook")
    dry_run = not args.send

    digest_text = digest_path.read_text(encoding="utf-8")
    message_text = build_message_text(digest_text, keyword)
    payload = build_payload(message_text, secret)

    send_result = {"ok": False, "http_status": None, "response": "dry-run"}
    result = "dry_run"
    if args.send:
        if not webhook_url:
            raise RuntimeError(f"FEISHU_WEBHOOK_URL is missing. Fill {env_file} or set the environment variable.")
        send_result = send_payload(webhook_url, payload, args.timeout)
        result = "sent" if send_result["ok"] else "send_failed"

    summary = {
        "generated_at": utc_now(),
        "local_generated_at": local_time_label(),
        "dry_run": dry_run,
        "digest": str(digest_path),
        "env_file": str(env_file),
        "webhook_configured": bool(webhook_url),
        "secret_configured": bool(secret),
        "message_characters": len(message_text),
        "result": result,
        "http_status": send_result.get("http_status"),
        "response": send_result.get("response"),
        "outputs": {
            "payload": str(output_dir / "latest-payload.json"),
            "json": str(output_dir / "latest-send-summary.json"),
            "markdown": str(output_dir / "latest-send-summary.md"),
        },
    }
    write_env_template(output_dir / "feishu.env.example")
    write_json(output_dir / "latest-payload.json", payload)
    write_json(output_dir / "latest-send-summary.json", summary)
    write_markdown(output_dir / "latest-send-summary.md", summary)
    print(json.dumps(summary, ensure_ascii=False, indent=2), flush=True)


if __name__ == "__main__":
    main()
