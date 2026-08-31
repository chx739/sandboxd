from __future__ import annotations

import hashlib
import re
from typing import Any


_API_KEY = re.compile(r"(?i)(api[_ -]?key\s*[:=]\s*)[^\s,;'”}]+")
_AUTHORIZATION = re.compile(
    r"(?i)(authorization\s*[:=]\s*bearer\s+)[^\s,;'”}]+"
)
_BEARER = re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]+")
_RAW_KEY = re.compile(r"\bsk-[A-Za-z0-9_-]{8,}\b")


def redact_text(value: str, limit: int | None = None) -> tuple[str, bool]:
    """脱敏常见凭据，同时告诉调用方内容是否发生变化。"""

    text = value
    for pattern, replacement in (
        (_API_KEY, r"\1[REDACTED]"),
        (_AUTHORIZATION, r"\1[REDACTED]"),
        (_BEARER, "[REDACTED]"),
        (_RAW_KEY, "[REDACTED]"),
    ):
        text = pattern.sub(replacement, text)
    if limit is not None:
        text = text[:limit]
    return text, text != value


def public_error(value: object, limit: int = 512) -> str:
    """把异常转成可返回、可落盘的有界文本，不保留凭据或掩码指纹。"""
    return redact_text(str(value), limit=limit)[0]


def safe_tool_arguments(name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    """Trace/Session 只保存文件正文的长度和摘要，不复制潜在秘密。"""

    safe = dict(arguments)
    sensitive_keys = {
        "write_file": ("content",),
        "edit_file": ("oldText", "newText"),
    }.get(name, ())
    for key in sensitive_keys:
        value = safe.get(key)
        if isinstance(value, str):
            encoded = value.encode("utf-8")
            safe[key] = {
                "redacted": True,
                "bytes": len(encoded),
                "sha256": hashlib.sha256(encoded).hexdigest(),
            }
    return safe
