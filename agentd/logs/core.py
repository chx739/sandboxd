from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


def timestamp(value: str) -> datetime:
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (TypeError, ValueError) as exc:
        raise ValueError("时间必须为 ISO8601") from exc
    if result.tzinfo is None:
        raise ValueError("时间必须包含时区")
    return result.astimezone(timezone.utc)


class LogRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    log_id: str = Field(pattern=r"^[a-zA-Z0-9_-]{1,80}$")
    timestamp: str
    service: str = Field(min_length=1, max_length=64)
    level: Literal["DEBUG", "INFO", "WARN", "ERROR"]
    error_code: str = Field(default="", max_length=64)
    trace_id: str = Field(default="", max_length=80)
    message: str = Field(min_length=1, max_length=2000)
    provenance: Literal["synthetic", "public", "captured"]

    @model_validator(mode="after")
    def valid_time(self) -> "LogRecord":
        timestamp(self.timestamp)
        return self


class LogQuery(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    start: str
    end: str
    service: str | None = Field(default=None, min_length=1, max_length=64)
    level: Literal["DEBUG", "INFO", "WARN", "ERROR"] | None = None
    error_code: str | None = Field(default=None, min_length=1, max_length=64)
    trace_id: str | None = Field(default=None, min_length=1, max_length=80)
    keyword: str | None = Field(default=None, min_length=1, max_length=128)
    limit: int = Field(default=3, ge=1, le=100)

    @model_validator(mode="after")
    def bounded_window(self) -> "LogQuery":
        delta = (timestamp(self.end) - timestamp(self.start)).total_seconds()
        if not 0 < delta <= 7 * 86400:
            raise ValueError("时间窗口必须大于零且不超过7天；语义为[start,end)")
        if self.keyword is not None and not self.keyword.strip():
            raise ValueError("keyword 不能只有空格")
        return self


class LogAggregation(LogQuery):
    group_by: Literal["service", "level", "error_code", "minute"] = "service"
    metric: Literal["count"] = "count"


def load_logs(path: Path) -> tuple[list[LogRecord], str]:
    rows = [LogRecord.model_validate(json.loads(line)) for line in path.read_text().splitlines() if line.strip()]
    if not rows or len({r.log_id for r in rows}) != len(rows):
        raise ValueError("日志为空或存在重复 log_id")
    canonical = json.dumps([r.model_dump() for r in sorted(rows, key=lambda r: r.log_id)],
                           ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return rows, hashlib.sha256(canonical.encode()).hexdigest()
