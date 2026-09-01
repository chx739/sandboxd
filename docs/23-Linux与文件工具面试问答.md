# Linux Connector 与文件工具问题索引

> **文档职责：只保留 Phase 4 问题和权威出处，不再维护完整答案。** 项目级回答统一查 [10 项目设计 FAQ](10-面试问答与项目讲法.md)；实现原理分别查 [21 Linux SSH Connector](21-Linux-SSH-Connector学习手册.md) 与 [22 Agent 原生文件工具](22-Agent原生文件工具学习手册.md)；代码入口查 [25 代码导读](25-代码导读与模块地图.md)。

## 1. Linux Connector

| 问题 | 核对出处 |
|---|---|
| 为什么不给 Agent 任意 Bash？ | [10 Q29](10-面试问答与项目讲法.md#q29为什么不给任意-bash)、[26 §14](26-Agent八股知识地图.md#14-为什么-agent-不能拿任意-bash) |
| `linux_read` 如何避免本地和远端命令注入？ | [21 §2 最小实现](21-Linux-SSH-Connector学习手册.md#2-项目里的最小实现)、[25 代码导读](25-代码导读与模块地图.md) |
| strict host key 与 Client Key 分别认证谁？ | [21 §4 Host Key 与用户 Key](21-Linux-SSH-Connector学习手册.md#host-key-与用户-key) |
| 为什么同时使用 authorized_keys command 和 sshd ForceCommand？ | [21 §4](21-Linux-SSH-Connector学习手册.md#4-必须掌握的基础知识)、[11 坑 46](11-开发踩坑与排障.md#46-sshd-降权后读不到-0700-key-目录中的-authorized_keys) |
| 如何证明远端不是任意 Shell？ | [Phase 9 evidence](evidence/phase9-full-regression.md)、[21 §9 动手验证](21-Linux-SSH-Connector学习手册.md#9-自己动手验证) |
| SSH 路径是否由 gVisor 隔离？ | [10 Q28](10-面试问答与项目讲法.md#q28linux-ssh-路径是否由-gvisor-隔离)、[24 §2](24-项目全景与心智模型.md#2-四层架构) |
| 为什么 host、user、port 和 Key 不允许由模型提交？ | [21 §5 为什么采用当前方案](21-Linux-SSH-Connector学习手册.md#5-为什么采用当前方案)、[26 §8](26-Agent八股知识地图.md#8-plugintool-和-connector) |
| Demo 和生产环境应怎样管理 SSH 凭据？ | [21 §8 高频问题](21-Linux-SSH-Connector学习手册.md#8-高频问题与回答思路)、[10 §10](10-面试问答与项目讲法.md#10-生产化差距) |
| 为什么没有引入 Paramiko/AsyncSSH？ | [21 §5 为什么采用当前方案](21-Linux-SSH-Connector学习手册.md#5-为什么采用当前方案)、[21 §6 未采用方案](21-Linux-SSH-Connector学习手册.md#6-考虑过但没有采用的方案) |

## 2. task 文件 Workspace

| 问题 | 核对出处 |
|---|---|
| 文件路径如何拒绝绝对路径、`..` 和 symlink 逃逸？ | [22 §4 基础知识](22-Agent原生文件工具学习手册.md#4-必须掌握的基础知识)、[25 代码导读](25-代码导读与模块地图.md) |
| 当前实现是否完全消除了 symlink TOCTOU？ | [22 §4 基础知识](22-Agent原生文件工具学习手册.md#4-必须掌握的基础知识)、[10 §10](10-面试问答与项目讲法.md#10-生产化差距) |
| 覆盖为什么要求 expectedSha256？ | [22 §8 高频问题](22-Agent原生文件工具学习手册.md#8-高频问题与回答思路)、[07 CAS](07-预热池与CAS.md) |
| `edit_file` 为什么要求 oldText 只出现一次？ | [22 §8 高频问题](22-Agent原生文件工具学习手册.md#8-高频问题与回答思路)、[22 §5](22-Agent原生文件工具学习手册.md#5-为什么采用当前方案) |
| 原子替换解决什么，不解决什么？ | [22 §4 原子替换](22-Agent原生文件工具学习手册.md#原子替换) |
| 读、搜索和 Diff 的模式脱敏是否等于完整 DLP？ | [22 §4 基础知识](22-Agent原生文件工具学习手册.md#4-必须掌握的基础知识)、[24 §9](24-项目全景与心智模型.md#9-当前做到了什么没有做到什么) |
| 为什么 Workspace 归 task，而不是 session？ | [24 §5](24-项目全景与心智模型.md#5-五类身份不要混)、[22 §5 为什么采用当前方案](22-Agent原生文件工具学习手册.md#5-为什么采用当前方案) |
| taskId、sessionId、sandboxId、targetId 各控制什么？ | [24 §5](24-项目全景与心智模型.md#5-五类身份不要混)、[25 代码导读](25-代码导读与模块地图.md) |
| 文件工具会不会修改外部生产系统？ | [10 Q30](10-面试问答与项目讲法.md#q30文件工具会修改生产系统吗)、[24 §7](24-项目全景与心智模型.md#7-九个工具按能力分类) |

## 3. 注入、证据与生产边界

| 问题 | 核对出处 |
|---|---|
| Prompt Injection 进入 Linux 日志后由什么边界限制？ | [10 Q26](10-面试问答与项目讲法.md#q26prompt-injection-被解决了吗)、[21 §5 为什么采用当前方案](21-Linux-SSH-Connector学习手册.md#5-为什么采用当前方案) |
| 任意 SSH、路径逃逸和错误 SHA 分别如何做负向验证？ | [Phase 9 evidence](evidence/phase9-full-regression.md)、[学习实验台账](evidence/learning-experiments.md) |
| 当前 Linux/File Demo 离生产还差什么？ | [10 §10](10-面试问答与项目讲法.md#10-生产化差距)、[21 §6](21-Linux-SSH-Connector学习手册.md#6-考虑过但没有采用的方案)、[22 §6](22-Agent原生文件工具学习手册.md#6-没有采用的方案) |
| Phase 4 最值得复盘的真实坑在哪里？ | [11 坑 45–47](11-开发踩坑与排障.md#45-tmpfs-会遮住镜像层里预创建的目录)、[21 §7 实际踩坑](21-Linux-SSH-Connector学习手册.md#7-实际踩坑) |

## 4. 自测标准

不看链接完成以下四项：

1. 画出 Kubernetes/gVisor 路径与 Linux/SSH 路径，指出两者不同的最终边界；
2. 解释 Policy、Connector、Host Key、低权限账号和 forced-command 分别防谁；
3. 区分 SHA256 CAS、原子 replace、路径约束和 symlink 防护的职责；
4. 给出一个真实成功证据和一个真实拒绝证据，不能只说“代码里做了判断”。
