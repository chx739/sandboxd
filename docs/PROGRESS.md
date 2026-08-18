# sandboxd 持续开发进度

> 本文件是跨会话进度快照。每完成一个可验证步骤就更新它，避免上下文压缩后重复工作或偏移目标。目标和边界以 `../GOAL.md` 为准。

## 当前阶段

阶段 4：实现单 worker 预热池、Workqueue reconcile 和 JSON Patch CAS 并发认领。

## 已完成

- 建立 GitHub 仓库、目标锚点和跨上下文续作协议。
- 完成 WSL2 用户目录工具链、Kubernetes 1.35.0 单节点 kind、gVisor release-20260810.0 和 Calico 3.32.0。
- 真实 gVisor、PSA restricted、只读 RBAC、NetworkPolicy 允许/拒绝路径均有实测证据。
- 实现 PodSpec Builder，安全基线测试、projected token/卷测试、vet 和 build 通过。
- 引入 client-go 0.35.0，与 api/apimachinery 0.35.0 保持 minor 一致。
- 实现 Manager：direct Pod 从创建时即为 busy，等待 Ready 只读 filtered informer/lister 缓存，删除使用 foreground propagation。
- 实现 Exec：WebSocket 优先，仅协议升级/代理错误 fallback SPDY；保留 exit code；超时使用独立 10 秒 context 删除 Pod。
- 实现基础 API：loopback 默认监听、Token 必填、常量时间鉴权、ID 校验、请求体/输出上限、create/list/delete/exec/health/ready。
- 一键真实验收通过：401、201、exit 0 stdout/stderr、exit 7、504 自动删除、204 显式删除，结束后 managed Pod=0。
- 发现并修复 informer lazy initialization 顺序问题，记录到 `docs/05-client-go与Exec.md`。
- 新增 `docs/11-开发踩坑与排障.md`，以现象/根因/解决证据/面试讲法持续记录真实坑。

## 正在进行

- 建立只有固定 key 的 workqueue 和单 reconcile worker。
- 统计 provisioning idle + Ready idle，维持目标池大小 2。
- 用 JSON Patch `test` + `replace` 实现原子认领。

## 紧接着做

1. Pool 复用 main 中同一个 informer，不重复 List/Watch。
2. event handler 只入队 `pool` 固定 key，业务逻辑全部放 reconcile。
3. reconcile 清理终态 Pod，按当前缓存状态补充或缩减 idle。
4. Claim 随机候选并做 CAS；池空时调用 Manager.Create direct fallback。
5. Release 直接删除，不复用可能被命令污染的 Pod。
6. 默认并发 5 验证无重复 ID，记录 claim conflict 和资源变化。
7. 完成 `docs/06-Informer与控制器模式.md`、`docs/07-预热池与CAS.md` 后提交推送。

## 资源与安全约束

- kind：单 control-plane 节点；预热池默认 2。
- 并发验证默认 5；资源确认充足后上限 10，不运行 20 并发日常测试。
- 可用内存接近 2 GiB、持续使用 swap 或 WSL/Docker 异常时停止测试。
- sudo 仅用于无法由普通用户完成的最小系统操作；密码不进入命令、脚本、日志或 Git。
- 只清理名称和来源都能确认属于 sandboxd 的容器、集群、网络和临时文件。

## 尚未开始

- Workqueue/Pool/CAS 的具体实现。
- Prometheus 指标、DryRun 计划和 Operator 审批门。
- 一键最终 Demo、最小并发验证、README 最终实测数据。
- 后续模块学习文档及最终面试问答手册。
