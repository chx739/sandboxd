# Phase 15：树形 Session MVP 本地证据

> 2026-09-28，Phase 7 M1。证据类型：本地 Python 确定性测试和合成 JSONL CLI；未运行 kind/gVisor 联合 E2E，未调用外部 LLM。

## 代码与输入

- `agentd/app/runtime/session.py`：追加 `session.node`、`session.head`，按父链恢复；只允许完整 Turn 分支；旧线性快照首次读取时迁移；末行写入中断时忽略并在下次追加前截断。
- `agentd/app/store.py`：从指定 nodeId 恢复，沿用 sessionId、生成新 taskId；`AgentRunner` 的原有 claim/release 负责本次新 sandbox。
- `agentd/app/main.py`：API Token 保护 tree/path/branch/resume。
- `agentd/testdata/session-tree-demo/session-0123456789abcdef.jsonl`：全合成双分支夹具，七个节点，无真实数据。

## 执行命令与观察

```text
uv run --project agentd --frozen -- python -m agentd.session_cli --session-dir agentd/testdata/session-tree-demo tree session-0123456789abcdef
结果：activeLeafId=node-0000000000000007，7 个节点，其中 0003 分出旧的 0004→0005 和新的 0006→0007。

uv run --project agentd --frozen -- python -m agentd.session_cli --session-dir agentd/testdata/session-tree-demo path session-0123456789abcdef node-0000000000000005
结果：祖先路径以 Old ending 结束，不包含 New ending。

uv run --project agentd --frozen -- python -m unittest discover -s agentd/tests -v
结果：Ran 40 tests in 1.986s；OK。
```

新增 Session 断言覆盖分支隔离、重启恢复、工具组未完成拒绝、工具组完成后可分支、旧线性快照迁移、截断尾行修复、选定节点恢复的新 task 身份；现有脱敏/权限断言仍通过。API 测试覆盖新端点需 API Token。

## 证据边界

这些是固定夹具与现有 FastAPI TestClient 的结果。未展示真实模型在分支后的回答质量，也未启动 sandboxd/kind 证明这一次具体分支确实获得新 gVisor Pod；新 sandbox 语义来自未改动的 `AgentRunner.run` claim/release 路径及既有 Replay 测试。后续联合演示应单独记录，不用本条替代。
