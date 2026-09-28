from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit


@dataclass(frozen=True)
class Settings:
    listen_host: str
    listen_port: int
    api_token: str
    alert_token: str
    prometheus_url: str
    sandboxd_url: str
    sandboxd_token: str
    llm_mode: str
    llm_base_url: str
    llm_model: str
    llm_api_key: str
    llm_thinking: str
    replay_file: Path
    trace_dir: Path
    linux_targets_file: Path | None = None
    workspace_dir: Path | None = None
    memory_root: Path | None = None
    memory_project: str = "sandboxd"
    retrieval_corpus: Path | None = None
    retrieval_queries: Path | None = None
    retrieval_embedding_dir: Path | None = None
    retrieval_reranker_dir: Path | None = None


def _required(name: str) -> str:
    value = os.getenv(name, "")
    if not value:
        raise ValueError("%s 不能为空" % name)
    return value


def _http_url(name: str, default: str | None = None) -> str:
    value = os.getenv(name, default or "").rstrip("/")
    parsed = urlsplit(value)
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.netloc
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
        or parsed.path not in {"", "/"}
    ):
        raise ValueError("%s 必须是没有认证信息、query 和额外 path 的 http(s) URL" % name)
    return value


def load_settings() -> Settings:
    project_dir = Path(__file__).resolve().parents[1]
    mode = os.getenv("AGENTD_LLM_MODE", "replay")
    if mode not in {"live", "replay"}:
        raise ValueError("AGENTD_LLM_MODE 只能是 live 或 replay")

    api_token = _required("AGENTD_TOKEN")
    alert_token = _required("AGENTD_ALERT_TOKEN")
    if api_token == alert_token:
        raise ValueError("AGENTD_TOKEN 与 AGENTD_ALERT_TOKEN 必须不同")

    llm_base_url = os.getenv("AGENTD_LLM_BASE_URL", "").rstrip("/")
    llm_model = os.getenv("AGENTD_LLM_MODEL", "")
    llm_api_key = os.getenv("AGENTD_LLM_API_KEY", "")
    llm_thinking = os.getenv("AGENTD_LLM_THINKING", "default")
    if llm_thinking not in {"default", "enabled", "disabled"}:
        raise ValueError("AGENTD_LLM_THINKING 只能是 default、enabled 或 disabled")
    if mode == "live":
        if not llm_base_url or not llm_model or not llm_api_key:
            raise ValueError("live 模式必须设置 LLM base URL、model 和 API key")
        # 模型 Endpoint 由部署者配置，但不能把凭证塞进 URL。
        parsed = urlsplit(llm_base_url)
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.netloc
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError("AGENTD_LLM_BASE_URL 必须是合法且不含认证信息的 http(s) URL")

    replay_file = Path(
        os.getenv(
            "AGENTD_REPLAY_FILE",
            str(project_dir / "testdata" / "injection-denied.replay.json"),
        )
    )
    trace_dir = Path(
        os.getenv(
            "AGENTD_TRACE_DIR",
            str(project_dir.parent / ".cache" / "agent-traces"),
        )
    )
    linux_targets_value = os.getenv("AGENTD_LINUX_TARGETS_FILE", "")
    workspace_value = os.getenv(
        "AGENTD_WORKSPACE_DIR",
        "/tmp/sandboxd-agent-workspaces",
    )
    memory_root_value = os.getenv("AGENTD_MEMORY_ROOT", "")
    memory_project = os.getenv("AGENTD_MEMORY_PROJECT", "sandboxd")
    retrieval_corpus = os.getenv("AGENTD_RETRIEVAL_CORPUS", "")
    retrieval_queries = os.getenv("AGENTD_RETRIEVAL_QUERIES", "")
    if bool(retrieval_corpus) != bool(retrieval_queries):
        raise ValueError("检索工具必须同时配置 corpus 与 queries")

    return Settings(
        listen_host=os.getenv("AGENTD_LISTEN_HOST", "127.0.0.1"),
        listen_port=int(os.getenv("AGENTD_LISTEN_PORT", "8090")),
        api_token=api_token,
        alert_token=alert_token,
        prometheus_url=_http_url("AGENTD_PROMETHEUS_URL", "http://127.0.0.1:9090"),
        sandboxd_url=_http_url("AGENTD_SANDBOXD_URL", "http://127.0.0.1:8080"),
        sandboxd_token=_required("SANDBOXD_TOKEN"),
        llm_mode=mode,
        llm_base_url=llm_base_url,
        llm_model=llm_model,
        llm_api_key=llm_api_key,
        llm_thinking=llm_thinking,
        replay_file=replay_file,
        trace_dir=trace_dir,
        linux_targets_file=(
            Path(linux_targets_value) if linux_targets_value else None
        ),
        workspace_dir=Path(workspace_value),
        memory_root=Path(memory_root_value) if memory_root_value else None,
        memory_project=memory_project,
        retrieval_corpus=Path(retrieval_corpus) if retrieval_corpus else None,
        retrieval_queries=Path(retrieval_queries) if retrieval_queries else None,
        retrieval_embedding_dir=Path(os.getenv(
            "AGENTD_RETRIEVAL_EMBEDDING_DIR",
            str(Path.home() / ".local/share/sandboxd/models/bge-small-en-v1.5-5c38ec7"),
        )),
        retrieval_reranker_dir=Path(os.getenv(
            "AGENTD_RETRIEVAL_RERANKER_DIR",
            str(Path.home() / ".local/share/sandboxd/models/bge-reranker-base-2cfc18c"),
        )),
    )
