# Phase 4：Linux Host 与原生文件工具实现计划

> 历史状态：Phase 4 M0–M5 已完成并合并到 `main`。本文保留 SSH、文件边界和前后 E2E 的设计约束；当前没有未完成里程碑。

## 1. 目标与证据顺序

在不修改 Go sandboxd 安全底座的前提下，为 agentd 增加受限 SSH Linux Connector 和 task 专属文件工作区。必须严格按以下顺序：

```text
Phase 3 HEAD 真实 Replay E2E
  -> 记录通过/失败事实
  -> LinuxHostPlugin
  -> 原生文件工具
  -> Replay/Policy/Trace 接线
  -> 文档和最小单测
  -> 同规格真实 Replay E2E
```

前一次 E2E 证明起点健康；后一次证明新功能没有破坏 Prometheus、Alertmanager、gVisor、Kubernetes、RBAC 和 Pending Plan 链路。两次都不调用 Live LLM。

## 2. 固定架构

```text
Agent Loop
  ├─ Runtime Native File Tools
  │    └─ FileWorkspace(taskId)
  │         list/read/search/write/edit
  ├─ PrometheusPlugin
  ├─ KubernetesPlugin -> sandboxd -> gVisor/RBAC/Plan
  └─ LinuxHostPlugin
       -> LinuxHostClient
       -> /usr/bin/ssh 固定 argv
       -> demo-linux Target
       -> low privilege + forced-command
```

SSH 不经过 gVisor。它的执行边界是固定 Target Registry、严格 Host Key、低权限远端用户和 forced-command。文档与面试中不得声称 gVisor 保护了 Linux Host Connector。

## 3. Tool 接口

### 3.1 LinuxHostPlugin

```json
{
  "name": "linux_read",
  "arguments": {
    "targetId": "demo-linux",
    "operation": "host_summary"
  }
}
```

operation 只有：

- `host_summary`：内核、uptime、负载和内存摘要；
- `process_list`：固定字段、最多 20 条；
- `disk_usage`：固定文件系统字段；
- `read_demo_log`：只读固定 Demo 日志，不接受路径。

模型不能提交网络地址、SSH 用户、端口、Key、命令、路径或参数。Connector 配置来自仓库外 0600 JSON 文件；私钥和 known_hosts 路径只在可信 Client 中使用，不进入 Tool Schema、Trace 或 Session。

### 3.2 原生文件工具

```text
list_files(path=".")
read_file(path, offset=0, limit=16384)
search_files(query, path=".")
write_file(path, content, expectedSha256?)
edit_file(path, oldText, newText, expectedSha256)
```

共同边界：

- 每个 taskId 一个 Linux 文件系统工作区，目录 0700、文件 0600；
- 相对路径长度和层级有上限，拒绝空组件、绝对路径、`..` 和 NUL；
- 逐级 `lstat` 拒绝符号链接，解析后再次检查仍在根目录；
- 文本文件最大 256 KiB，单次模型 Observation 仍受 4 KiB 总边界；
- `search_files` 只做字面量搜索，最多扫描 100 个文件/1 MiB、返回 50 条；
- 现有文件覆盖必须带匹配的 SHA256；`edit_file` 的 oldText 必须恰好出现一次；
- 写入先落同目录临时文件，再 `os.replace`，返回 old/new hash 与有界 unified diff。

第一版不做 tar、二进制、远端同步、文件 API、Session 共享工作区或后台清理器。

## 4. 文件结构

```text
agentd/app/
  clients.py                  LinuxHostClient、TargetConfig
  plugins/linux_host.py       linux_read Schema 与分派
  tools/files.py              FileWorkspace 与五个原生工具
  plugins/base.py             PluginContext 增加 host/workspace 窄对象
  policy.py                   Linux/file 前置参数策略
  runner.py                   每个 Task 注入对应 Workspace
deploy/linux-target/
  Dockerfile                  复用本地固定镜像，不在线安装包
  entrypoint.sh               复制临时 Key 后启动 sshd
  forced-command.sh           远端最终只读白名单
  sshd_config                 禁密码、禁转发、禁 TTY
hack/run-linux-agent-demo.sh  启停一次性 Target，复用 run-agent-demo 主链
agentd/tests/
  test_files.py
  test_linux_host.py
docs/
  21-Linux-SSH-Connector学习手册.md
  22-Agent原生文件工具学习手册.md
  23-Linux与文件工具面试问答.md
```

若本地固定镜像没有 sshd，必须停止并重新评估；不得静默改用真实宿主机或不校验 Host Key。

## 5. Demo Target

- 只使用本项目确认创建的 `sandboxd-linux-target-<runId>` 容器；
- 复用本地已存在并固定 digest 的镜像，禁止为了测试随意拉 `latest`；
- CPU、内存、PID 设小上限，只绑定随机的 `127.0.0.1` 端口；
- Client Key、Host Key、known_hosts、Target JSON 都放 `/tmp` 的 0700 临时目录，退出精确删除；
- authorized_keys 使用 forced-command 与 no-forwarding/no-pty；远端用户无 sudo；
- 日志 Fixture 含一条间接 Prompt Injection，用于证明 SSH Observation 也是不可信输入。

## 6. 里程碑

| 阶段 | 内容 | 完成判据 |
|---|---|---|
| M0 | 目标、计划、分支、资源审计 | GOAL/AGENTS/PROGRESS 可恢复 |
| M1 | 改造前真实 E2E | Phase 3 Replay 全链路通过并清理 |
| M2 | LinuxHostPlugin 与 Target | 固定 operation 成功，任意命令两层拒绝 |
| M3 | 五个文件工具 | 路径/链接/CAS/Diff 单测通过 |
| M4 | Runtime/Replay/E2E 接线 | Trace 含 Linux 与文件工具，旧拒绝矩阵不退化 |
| M5 | 学习/面试/踩坑与最终验证 | 前后 E2E、Go/Python、秘密扫描、GitHub 完成 |

## 7. 非目标

- 任意 Bash、PowerShell、SSH command、host/user/port；
- 真实宿主机、真实生产主机、sudo、systemctl restart、kill、安装包；
- 远端文件读写、SFTP、SCP、tar、对象存储；
- 动态插件、Target 在线注册、多租户凭据库、堡垒机、SSH CA；
- 文件监听、RAG、向量库、二进制解析、Session 树共享文件；
- 修改 Go sandboxd 或宣称 SSH 路径由 gVisor 隔离。

## 8. 上下文恢复

1. 读 `GOAL.md` 的 Phase 4；
2. 读本文；
3. 读 `docs/PROGRESS.md` 的 Phase 4 下一步；
4. 查看当前分支、git status、最近提交；
5. 只继续第一个未完成里程碑，不跳过改造前/后 E2E。
