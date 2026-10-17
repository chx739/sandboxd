"""三类运维问题经过真实日志/检索工具与现有 AgentRunner 的确定性回放。"""
from __future__ import annotations
import argparse
import asyncio
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any, Sequence

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage, ToolMessage
from .app.memory import ExplicitExtractor, MemoryStore
from .app.model_gateway import ModelInvocation
from .app.models import AlertEvent, ModelUsage
from .app.plugins.knowledge import KnowledgePlugin
from .app.plugins.logs import LogsPlugin
from .app.plugins.registry import build_builtin_registry
from .app.router import ChoiceJudgement, FakeChoiceSource, ModelRouter
from .app.runner import AgentRunner
from .app.runtime.session import SessionJournal
from .logs.core import LogQuery, LogAggregation, load_logs
from .logs.oracle import expected
from .phase7_demo import _Sandbox

ROOT = Path(__file__).resolve().parent
MODELS = Path.home() / '.local/share/sandboxd/models'
WINDOW = {'start': '2026-09-01T00:15:00Z', 'end': '2026-09-01T00:30:00Z'}
SCENARIOS = [
    {'id': 'payments-memory', 'service': 'payments', 'code': 'OOM_KILLED',
     'summary': '回放：payments 在 00:15–00:30 UTC 出现异常，请查日志并给出验证步骤。',
     'query': 'OOM_KILLED container memory termination reason memory limits diagnosis',
     'docs': ['fixture-payments-memory', 'k8s-manage-resources-containers'],
     'hypothesis': '可能与容器内存限制或内存压力有关；日志标签不能证明实际 OOM 或泄漏。',
     'checks': '核对容器 last termination reason、memory limit 与同期内存峰值；缺少实际 Pod 状态和内存指标。'},
    {'id': 'checkout-service', 'service': 'checkout', 'code': 'CONNECTION_REFUSED',
     'summary': '回放：checkout 在 00:15–00:30 UTC 请求上游失败，请查日志并给出验证步骤。',
     'query': 'Service connection refused selector targetPort EndpointSlices backend Pods diagnosis',
     'docs': ['fixture-checkout-service', 'k8s-debug-service'],
     'hypothesis': '可能是目标端口、后端监听或服务端点异常，需要现场验证。',
     'checks': '核对 Service selector、targetPort、EndpointSlices 和后端就绪；缺少实际 Service 配置和 Pod 状态。'},
    {'id': 'catalog-readiness', 'service': 'catalog', 'code': 'READINESS_FAILED',
     'summary': '回放：catalog 在 00:15–00:30 UTC 就绪检查异常，请查日志并给出验证步骤。',
     'query': 'KubePodNotReady readiness probe failed running pod diagnosis',
     'docs': ['runbook-KubePodNotReady', 'k8s-pod-lifecycle'],
     'hypothesis': '可能未满足 readiness 条件；这不等于已经证明进程死亡或需要重启。',
     'checks': '核对 readiness probe、Pod conditions、events 和应用响应；缺少实际探针配置与 Pod 事件。'},
]


class OpsReplay:
    """脚本选择工具，读取真实返回值再引用；不是 LLM 能力评测。"""
    mode = 'replay'
    model_name = 'ops-scripted-replay'
    provider_name = 'deterministic-replay'
    capabilities = {'toolCalling': True, 'deterministic': True}

    def __init__(self, case: dict):
        self.case = case

    def new_session(self, tool_schemas: Sequence[dict]) -> 'OpsReplaySession':
        return OpsReplaySession(self.case)


class OpsReplaySession:
    def __init__(self, case: dict):
        self.case, self.step = case, 0

    async def invoke(self, messages: Sequence[BaseMessage]) -> ModelInvocation:
        self.step += 1
        args = {**WINDOW, 'service': self.case['service'], 'level': 'ERROR', 'limit': 3}
        bodies = {}
        for message in messages:
            if isinstance(message, ToolMessage):
                payload = json.loads(str(message.content))
                if not payload.get('ok'):
                    raise RuntimeError('工具失败，回放拒绝编造证据')
                bodies[message.name or message.tool_call_id] = payload['body']
        if self.step == 1:
            tool, arguments = 'search_logs', args
        elif self.step == 2:
            tool, arguments = 'aggregate_logs', {**args, 'group_by': 'error_code'}
        elif self.step == 3:
            logs = next(b for b in bodies.values() if 'logs' in b)
            code = logs['logs'][0]['error_code'] if logs['logs'] else ''
            # 从观察得到的错误码选择固定检索问题；未知现场不能继续预编结论。
            target = next((c for c in SCENARIOS if c['code'] == code), None)
            if target is None:
                raise RuntimeError('未发现此回放覆盖的错误码')
            tool, arguments = 'search_knowledge', {'query': target['query'], 'topK': 3}
        else:
            logs = next(b for b in bodies.values() if 'logs' in b)
            knowledge = next(b for b in bodies.values() if 'evidence' in b)
            aggregation = next(b for b in bodies.values() if 'buckets' in b)
            refs = [f"log:{row['log_id']}@{row['timestamp']}" for row in logs['logs']]
            refs += [f"chunk:{row['chunkId']}" for row in knowledge['evidence']]
            payload = {
                'summary': f"[确定性回放] 观察事实：{self.case['service']} 在指定窗口有 {aggregation['total']} 条 ERROR 日志。引用：" + '; '.join(refs),
                'rootCause': '待验证假设：' + self.case['hypothesis'], 'severity': 'warning',
                'recommendation': '验证步骤与缺失信息：' + self.case['checks'] + ' 本演示未执行修复。',
            }
            return ModelInvocation(AIMessage(content=json.dumps(payload, ensure_ascii=False)), ModelUsage(), 'stop', 0)
        call = {'name': tool, 'args': arguments, 'id': f'ops-call-{self.step}', 'type': 'tool_call'}
        return ModelInvocation(AIMessage(content='', tool_calls=[call]), ModelUsage(), 'tool_calls', 0)


def score_trace(case: dict, trace: Any, diagnosis: Any, rows: list) -> dict:
    steps = [s for s in trace.steps if s.tool]
    payloads = [json.loads(s.observation) for s in steps]
    all_ok = all(p.get('ok') and not s.denied for s, p in zip(steps, payloads))
    bodies = {s.tool: p.get('body', {}) for s, p in zip(steps, payloads)}
    log = bodies.get('search_logs', {})
    wanted = expected(rows, LogQuery(**WINDOW, service=case['service'], level='ERROR', limit=3))
    # 独立 oracle 验证现场日志，不以 Agent 自报的结果作为事实。
    log_ids = [r['log_id'] for r in log.get('logs', [])]
    evidence = bodies.get('search_knowledge', {}).get('evidence', [])
    cited = diagnosis.summary
    return {
        'toolOrderCorrect': [s.tool for s in steps] == ['search_logs', 'aggregate_logs', 'search_knowledge'],
        'toolParametersCorrect': len(steps) == 3 and steps[0].arguments == {**WINDOW, 'service': case['service'], 'level': 'ERROR', 'limit': 3}
                                 and steps[1].arguments == {**WINDOW, 'service': case['service'], 'level': 'ERROR', 'limit': 3, 'group_by': 'error_code'}
                                 and steps[2].arguments == {'query': case['query'], 'topK': 3},
        'toolsSucceeded': all_ok,
        'necessaryLogsObtained': log_ids == wanted['ids'] and log.get('total') == wanted['total'],
        'aggregateCorrect': bodies.get('aggregate_logs', {}).get('buckets') == expected(rows, LogAggregation(**WINDOW, service=case['service'], level='ERROR', group_by='error_code'))['buckets'] and bodies.get('aggregate_logs', {}).get('total') == wanted['total'],
        'applicableDocumentRetrieved': any(e['docId'] in case['docs'] for e in evidence),
        'citationIdsResolve': bool(log_ids and evidence) and all('log:' + i in cited for i in log_ids)
                              and all('chunk:' + e['chunkId'] in cited for e in evidence),
        'factHypothesisVerificationSeparated': all(x in diagnosis.summary + diagnosis.root_cause + diagnosis.recommendation
                                                   for x in ['观察事实', '待验证假设', '验证步骤', '缺少', '未执行修复']),
    }


async def run() -> dict:
    lookup = KnowledgePlugin(ROOT / 'retrieval/data/ops-v1/corpus.jsonl', None,
                             MODELS / 'multilingual-e5-small-614241f', MODELS / 'bge-reranker-base-2cfc18c')
    logs = LogsPlugin(ROOT / 'logs/data/logs.jsonl')
    rows, log_hash = load_logs(logs.path)
    reports = []
    try:
        # 服务/模型初始化不算成每次 Agent 的在线运行时间。
        await asyncio.to_thread(lookup._load)
        with TemporaryDirectory(dir='/tmp', prefix='sandboxd-ops-') as directory:
            root = Path(directory)
            memory = MemoryStore(root / 'memory', 'sandboxd')
            seed = SessionJournal(root / 'sessions', 'session-0000000000000010')
            await seed.initialize('task-seed', AlertEvent())
            await seed.append_transcript('task-seed', [SystemMessage(content='Static policy'),
                HumanMessage(content='MEMORY[preference] response_language=zh'), AIMessage(content='已记录显式偏好')])
            await seed.append_result('task-seed', 'succeeded')
            await memory.extract_session(seed, ExplicitExtractor()); memory.consolidate()
            for index, case in enumerate(SCENARIOS, 11):
                gateway = OpsReplay(case)
                router = ModelRouter({'economy': gateway, 'strong': gateway},
                    FakeChoiceSource({case['summary']: ChoiceJudgement('strong', 0.95)}))
                sandbox = _Sandbox()
                journal = SessionJournal(root / 'sessions', f'session-{index:016x}')
                alert = AlertEvent(annotations={'summary': case['summary']})
                task_id = 'task-ops-' + case['id']
                await journal.initialize(task_id, alert)
                runner = AgentRunner(None, sandbox, gateway, build_builtin_registry(memory, lookup, logs),
                                     workspace_root=root / 'workspaces', memory_store=memory, model_router=router)
                diagnosis, trace, status = await runner.run(task_id, alert, journal=journal)
                await journal.append_result(task_id, status, diagnosis.summary)
                tree = await journal.tree()
                _, prefix, selected = await journal.load_branch(tree['activeLeafId'])
                score = score_trace(case, trace, diagnosis, rows)
                score.update(statusSucceeded=status == 'succeeded', branchRecoverable=bool(prefix) and selected == tree['activeLeafId'],
                             sandboxReleased=sandbox.released == ['phase7-fake-sandbox'],
                             routedOnce=sum(e.type == 'model.route.completed' for e in trace.events) == 1)
                reports.append({'id': case['id'], 'scores': score, 'passed': all(score.values()),
                                'diagnosis': diagnosis.model_dump(by_alias=True), 'trace': trace.model_dump(mode='json', by_alias=True)})
    finally:
        lookup.close(); logs.close()
    return {'kind': 'deterministic-agent-replay-real-local-stores', 'logSnapshotHash': log_hash,
            'modelCalls': 'scripted', 'jev': 'fake', 'sandbox': 'fake-lifecycle-only', 'externalModelCalls': 0,
            'scope': 'No live cluster or repair; natural-language decisions and semantic citation entailment not evaluated',
            'passed': sum(r['passed'] for r in reports), 'count': len(reports), 'cases': reports}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(); parser.add_argument('--output', type=Path)
    args = parser.parse_args(); report = asyncio.run(run())
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps({k: v for k, v in report.items() if k != 'cases'}, ensure_ascii=False, indent=2))
    raise SystemExit(0 if report['passed'] == report['count'] else 1)
