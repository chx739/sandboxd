# Linux SSH Connector 学习手册

## 1. 这个模块解决什么问题

Prometheus 告警只能说明“可能有故障”，Kubernetes Tool 也只能查看集群对象。真实运维还常要读取 Linux 主机的负载、进程、磁盘和固定日志。直接给模型 Bash 或 SSH 等于把宿主权限交给概率系统，因此本项目只暴露：

```text
linux_read(targetId, operation)
```

模型看不到 host、port、user、Key、known_hosts、命令和路径。

## 2. 项目里的最小实现

```text
LLM Tool Call
  -> policy.py：参数形状和 operation 白名单
  -> LinuxHostPlugin：只转发 targetId + operation
  -> LinuxHostClient：静态 Registry + 固定 /usr/bin/ssh argv
  -> sshd：公钥、StrictHostKey、禁止密码/TTY/转发
  -> authorized_keys command + sshd ForceCommand
  -> forced-command.sh：四个精确 case
```

四个操作是 `host_summary`、`process_list`、`disk_usage`、`read_demo_log`。前两层拒绝发生在本地，最后一层证明即使绕过 Python Policy，远端也不是任意 Shell。

SSH 路径**不经过 gVisor**。gVisor 保护的是 sandboxd 创建的 Kubernetes Sandbox Pod；Linux Connector 的边界是 SSH 身份和远端命令收缩。

## 3. 代码阅读顺序

1. `agentd/app/plugins/linux_host.py`：先看模型能提交什么；
2. `agentd/app/policy.py`：看 Agent 前置拒绝；
3. `agentd/app/clients.py` 的 `LinuxHostClient`：看固定 argv、输出和超时；
4. `deploy/linux-target/forced-command.sh`：看远端最终能力；
5. `deploy/linux-target/sshd_config`：看认证与转发限制；
6. `hack/run-linux-agent-demo.sh`：看临时 Key、Host Key 和真实 E2E。

## 4. 必须掌握的基础知识

### Host Key 与用户 Key

- Host Key 让 Client 确认“连接的是登记过的服务器”，防中间人；
- Client Key 让服务器确认“调用方持有获准私钥”；
- 两者解决相反方向的身份认证，不能用 `StrictHostKeyChecking=no` 省掉前者。

### forced-command

OpenSSH 会把客户端提交的命令放入 `SSH_ORIGINAL_COMMAND`。本项目不 `eval` 它，只做四个完整字符串 `case`。authorized_keys 的 `command=` 和 sshd `ForceCommand` 各限制一次。

### 为什么不用 Shell 字符串

`create_subprocess_exec(*argv)` 直接传 argv，不经过 `/bin/sh`；目标配置又拒绝空白、控制字符和前导 `-`。这避免命令替换、管道、重定向和引号逃逸。

### 退出码、超时与输出上限

Connector 同时有 10 秒总超时、32 KiB stdout、8 KiB stderr 上限。非零退出只向 Tool 返回通用错误，不回显可能含私钥路径的 OpenSSH stderr。

## 5. 为什么采用当前方案

- 使用系统 `/usr/bin/ssh`：协议实现成熟，不增加 Paramiko/AsyncSSH 依赖；
- 静态 Target JSON：代码审查能确定目标集合，没有在线注册面；
- 低权限一次性容器：可以真实验证 SSH，又不碰 WSL 宿主机；
- 双 forced-command：演示纵深防御，不把 Tool Schema 当授权边界；
- 四个 operation：足够讲 Linux 排障，又避免做成生产平台。

## 6. 考虑过但没有采用的方案

- 任意 `run_command`：能力强，但无法证明模型只能只读；
- sudo 白名单：配置复杂，Demo 很容易误触真实系统；
- SSH 密码：不利于自动化、轮转和最小权限；
- `StrictHostKeyChecking=no`：会让中间人拿到诊断数据和命令入口；
- 把 SSH 也塞进 gVisor：可以做，但会引入网络出口、Key 注入和额外镜像，不符合最小目标；
- 堡垒机、SSH CA、Vault：生产有价值，秋招 Demo 成本过高。

## 7. 实际踩坑

1. BuildKit 的 `FROM repo@digest` 在离线模式仍会解析远端 metadata；最终改为构建前校验本地 image ID，再使用固定 tag。
2. `/run` tmpfs 会遮住镜像层中的 `/run/sshd`，入口必须重建 privilege-separation 目录。
3. sshd 降权后不能穿过宿主 0700 Key 目录读取 authorized_keys；入口只复制公钥到 `/run/sshd`，私钥目录不放宽。
4. 当前 Docker 用 `127.0.0.1:0:22` 分配随机本地端口，`127.0.0.1::22` 没有得到期望映射。

## 8. 高频问题与回答思路

**Q：三层白名单是不是重复？**

不是。Policy 防模型误用，Connector 防内部直调，forced-command 防客户端或本地进程被绕过。它们位于不同信任边界。

**Q：有 SSH 就算通用运维了吗？**

目前只是“可扩展的外部 Linux 只读 Connector”证据，不是生产级主机平台。真实系统还需要租户归属、凭据轮转、堡垒机、审计存储和审批。

**Q：为什么 SSH 日志仍算 Prompt Injection？**

日志由外部进程生成，可能被攻击者控制。即使传输可信，内容仍不可信；E2E 中 Linux 日志的指令只进入证据，不能扩大工具权限。

## 9. 自己动手验证

```bash
uv run --project agentd --frozen python -m unittest agentd.tests.test_linux_host
./hack/run-linux-agent-demo.sh
```

观察 Trace 中 `pluginId=linux-host`、`statusCode=200` 和 `injectedVia=linux_log`。脚本还直接提交 `cat /etc/passwd`，必须由远端 forced-command 返回 126。

## 10. 一分钟项目讲法

“我没有给运维 Agent 任意 SSH，而是把模型接口缩成 targetId 加四个只读 operation。目标地址和私钥来自仓库外 0600 静态配置，Client 用 strict host key 和固定 argv，禁用密码、TTY 与全部转发；远端低权限账号再用 authorized_keys command 和 sshd ForceCommand 双重收口。真实 E2E 里日志注入进入上下文，但任意 SSH 命令返回 126。它和 Kubernetes gVisor 是两条不同信任边界，我不会声称 SSH 受 gVisor 保护。”
