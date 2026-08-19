from __future__ import annotations

import re


_API_KEY = re.compile(r"(?i)(api[_ -]?key\s*[:=]\s*)[^\s,;'”}]+")
_AUTHORIZATION = re.compile(
    r"(?i)(authorization\s*[:=]\s*bearer\s+)[^\s,;'”}]+"
)
_BEARER = re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]+")
_RAW_KEY = re.compile(r"\bsk-[A-Za-z0-9_-]{8,}\b")


def public_error(value: object, limit: int = 512) -> str:
    """把异常转成可返回、可落盘的有界文本，不保留凭据或掩码指纹。"""
    text = str(value)
    text = _API_KEY.sub(r"\1[REDACTED]", text)
    text = _AUTHORIZATION.sub(r"\1[REDACTED]", text)
    text = _BEARER.sub("[REDACTED]", text)
    text = _RAW_KEY.sub("[REDACTED]", text)
    return text[:limit]
