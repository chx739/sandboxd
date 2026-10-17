"""四模块无密钥联合演示：树形 Session、记忆、真实本机 RAG、Fake 路由。"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from .app.memory import ExplicitExtractor, MemoryStore
from .app.model_gateway import ReplayModelGateway
from .app.models import AlertEvent
from .app.plugins.knowledge import KnowledgePlugin
from .app.plugins.registry import build_builtin_registry
from .app.router import ChoiceJudgement, FakeChoiceSource, ModelRouter
from .app.runner import AgentRunner
from .app.runtime.session import SessionJournal

_ROOT = Path(__file__).resolve().parent
_MODELS = Path.home() / ".local/share/sandboxd/models"


class _Sandbox:
    def __init__(self) -> None:
        self.released: list[str] = []

    async def claim(self) -> dict[str, str]:
        return {"id": "phase7-fake-sandbox"}

    async def release(self, sandbox_id: str) -> None:
        self.released.append(sandbox_id)


async def run() -> dict[str, Any]:
    corpus = _ROOT / "retrieval/data/runbook-v1.corpus.jsonl"
    queries = _ROOT / "retrieval/data/runbook-v1.queries.jsonl"
    fixture = _ROOT / "testdata/phase7-integrated.replay.json"
    lookup = KnowledgePlugin(
        corpus, queries,
        _MODELS / "bge-small-en-v1.5-5c38ec7",
        _MODELS / "bge-reranker-base-2cfc18c",
    )
    try:
        with TemporaryDirectory(dir="/tmp") as directory:
            root = Path(directory)
            memory = MemoryStore(root / "memory", "sandboxd")
            seed = SessionJournal(root / "sessions", "session-0000000000000001")
            await seed.initialize("task-memory-seed", AlertEvent())
            await seed.append_transcript("task-memory-seed", [
                SystemMessage(content="Static safety policy"),
                HumanMessage(content="MEMORY[preference] response_language=zh"),
                AIMessage(content="记住回答语言偏好"),
            ])
            await seed.append_result("task-memory-seed", "succeeded")
            await memory.extract_session(seed, ExplicitExtractor())
            memory.consolidate()

            current = SessionJournal(root / "sessions", "session-0000000000000002")
            alert = AlertEvent(annotations={"summary": "CrashLoopBackOff runbook lookup"})
            await current.initialize("task-phase7-demo", alert)
            economy = ReplayModelGateway(fixture)
            strong = ReplayModelGateway(fixture)
            strong.model_name = "phase7-replay-strong"
            router = ModelRouter(
                {"economy": economy, "strong": strong},
                FakeChoiceSource({alert.annotations["summary"]: ChoiceJudgement("economy", 0.95)}),
            )
            sandbox = _Sandbox()
            runner = AgentRunner(
                None, sandbox, strong,
                build_builtin_registry(memory, lookup),
                workspace_root=root / "workspaces",
                memory_store=memory,
                model_router=router,
            )
            diagnosis, trace, status = await runner.run(
                "task-phase7-demo", alert, journal=current,
            )
            await current.append_result("task-phase7-demo", status, diagnosis.summary)
            path = await current.active_path()
            tree = await current.tree()
            _, prefix, selected = await current.load_branch(tree["activeLeafId"])
            tool_text = "\n".join(
                str(node["message"].get("content", ""))
                for node in path if node["message"]["role"] == "tool"
            )
            memory_text = "\n".join(
                str(node["message"].get("content", ""))
                for node in path if node["message"]["role"] == "user"
            )
            report = {
                "kind": "local-docker-rag-plus-deterministic-agent-replay",
                "status": status,
                "route": trace.routing,
                "model": trace.model,
                "searchToolCompleted": any(
                    step.tool == "search_knowledge" and not step.denied
                    for step in trace.steps
                ),
                "retrievalTrustMarked": "untrusted-retrieved-evidence" in tool_text,
                "memorySummaryInjectedAsData": "historical-memory" in memory_text,
                "sessionNodeCount": len(tree["nodes"]),
                "branchableLeaf": selected == tree["activeLeafId"] and bool(prefix),
                "sandboxReleased": sandbox.released == ["phase7-fake-sandbox"],
                "externalModelCalls": 0,
            }
            required = (
                report["status"] == "succeeded",
                report["route"]["effectiveTier"] == "economy",
                report["searchToolCompleted"],
                report["retrievalTrustMarked"],
                report["memorySummaryInjectedAsData"],
                report["branchableLeaf"],
                report["sandboxReleased"],
            )
            if not all(required):
                raise RuntimeError("Phase 7 联合 Replay 未满足全部接线断言")
            return report
    finally:
        lookup.close()


def main() -> None:
    print(json.dumps(asyncio.run(run()), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
