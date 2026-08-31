# Linux Connector 与文件工具面试问答

## 先讲清项目定位

这是秋招导向的单机 Demo：用真实 SSH 证明外部 Linux 只读诊断，用 task 工作区证明 Agent 原生文件能力，同时保留 gVisor Kubernetes 查询和审批链。它不冒充生产级主机平台。

## 高频问答

### 1. 为什么不给 Agent 任意 Bash？

Tool Calling 是模型输出协议，不是权限边界。任意 Bash 会把命令注入、路径逃逸、网络访问和凭据读取合并成一个无法穷举的面。固定 operation 更容易做服务端验证和审计。

### 2. `linux_read` 如何防命令注入？

模型只能传两个字段；Policy 校验 operation；Client 使用 `create_subprocess_exec` 固定 argv、不走 Shell；远端脚本只精确匹配四个字符串且不 eval。

### 3. strict host key 解决什么？

它认证服务器身份，避免连到中间人。Client 私钥只认证客户端，不能替代 Host Key。

### 4. 为什么同时用 authorized_keys command 和 sshd ForceCommand？

演示纵深防御。某一处配置被误删时，另一处仍将会话导向同一个固定脚本；最终脚本还会对 operation 精确匹配。

### 5. 如何证明远端不是任意 Shell？

E2E 绕过 Agent Policy，直接通过同一 Key 提交 `cat /etc/passwd`，forced-command 返回 126；正常 `host_summary` 和 `read_demo_log` 成功。

### 6. SSH 是否由 gVisor 隔离？

否。SSH 从 agentd 进程连接 Linux Target。gVisor 只保护 sandboxd 创建的 Kubernetes Sandbox Pod。这两条路径的身份、权限和证据必须分别描述。

### 7. Target 配置为什么不放 Tool 参数？

host/user/port/Key 一旦由模型决定，就会出现 SSRF、横向移动和凭据选择问题。静态 Registry 把选择限制为 targetId。

### 8. SSH 凭据怎么保存？

Demo 每次在 `/tmp` 0700 目录生成 Client/Host Key，配置和文件 0600，退出精确删除；Git、Trace、Session 都不保存私钥。生产应接 Vault、SSH CA 或堡垒机。

### 9. 为什么不用 Paramiko？

系统 OpenSSH 已存在且成熟，固定 argv 足够完成四个操作。增加 Python SSH 库会增加依赖和协议维护面，当前收益不高。

### 10. 文件路径如何防穿越？

限制相对路径，拒绝 `..`、绝对路径、空组件、反斜杠和 NUL；逐级 lstat 拒绝 symlink；解析后再次确认位于 task root；最终 open 使用 `O_NOFOLLOW`。

### 11. 是否完全没有 symlink TOCTOU？

不是。教学实现比普通 Path 拼接安全，但逐级检查和打开间仍有竞态。生产可用 openat2 的 `RESOLVE_BENEATH/NO_SYMLINKS`，或把 Workspace 放进独立 mount namespace。

### 12. 为什么覆盖必须带 SHA256？

实现乐观并发控制。模型根据旧内容做决定时，如果文件已变化，hash 不匹配就拒绝，避免 lost update。

### 13. `edit_file` 为什么要求 oldText 只出现一次？

避免模型说“改这一段”却替换多个位置。唯一匹配让变更目标确定，diff 更容易人工复核。

### 14. 原子替换如何实现？

同目录创建 0600 临时文件，完整写入并 fsync，再 `os.replace`。同文件系统 rename 原子，失败时不会留下半个目标文件。

### 15. 文件工具会泄露秘密吗？

常见 API Key/Bearer 在读、搜索、diff 中做模式脱敏；write/edit 参数在 Trace/Session 只保存 hash 和长度。但它不是完整 DLP，所以工作区仍不能存生产秘密。

### 16. 为什么 Workspace 属于 task 而不是 session？

task 是一次运行，边界最简单；session resume 会新建 Sandbox，也新建 Workspace。共享文件需要额外定义所有权、并发 CAS、配额和清理，第一版不做。

### 17. 多任务身份体现在哪里？

taskId 决定工作区目录；sessionId 决定线性会话日志；sandboxId 决定一次 gVisor 租约；targetId 决定外部 Linux 目标。四个 ID 生命周期不同。

### 18. Prompt Injection 进入 Linux 日志怎么办？

把日志标记为 untrusted evidence。E2E 让注入真实进入 `injectedVia=linux_log`，但模型可见工具仍没有任意命令，远端 forced-command 也不接受注入文本。

### 19. 这套方案离生产还差什么？

多租户 target 归属、凭据轮转、SSH CA/堡垒机、审计数据库、细粒度审批、连接池、主机健康、配额、openat2、Workspace 清理和灾备。

### 20. 这个阶段最值得讲的坑是什么？

三选一：BuildKit 离线仍解析 digest；tmpfs 遮住镜像目录；sshd 降权后读不到 0700 挂载中的 authorized_keys。回答要包含现象、根因、为什么没有通过放宽权限糊过去、最终证据。

## 90 秒项目讲法

“项目原本能从 Alertmanager 触发 Agent，查询 Prometheus，并在 gVisor Sandbox 中用只读 RBAC 查 Kubernetes。为了避免只能运维本集群，我新增了外部 Linux Connector，但没有开放通用 SSH：模型只传 targetId 和四个 operation，客户端 strict host key、固定 argv、禁密码/TTY/转发，远端低权限账号再用双 forced-command 收口。与此同时参考 Coding Agent 增加五个原生文件工具，每个 task 独立 0700 工作区，路径拒绝穿越和 symlink，覆盖用 SHA256 CAS，写入原子替换并返回脱敏 diff。真实 Replay E2E 中 Linux 和 Pod 日志注入都进入上下文，但任意 SSH、Kubernetes 删除和审批仍分别被 forced-command、Policy/RBAC 和双 Token 门拒绝。”

## 自测标准

能不看文档回答以下四题才算学会：

1. SSH 路径为什么不受 gVisor 保护？
2. Policy、Connector、forced-command 分别防谁？
3. SHA256 CAS 与原子 replace 分别解决什么？
4. taskId、sessionId、sandboxId、targetId 的生命周期有什么不同？
