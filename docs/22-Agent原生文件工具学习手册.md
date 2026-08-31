# Agent 原生文件工具学习手册

## 1. 这个模块解决什么问题

运维 Agent 需要整理诊断笔记、生成配置草案和分阶段编辑内容。只返回聊天文本难以支持后续 Tool Call；给它整个宿主文件系统又会暴露源码、凭据和用户文件。因此每次 Task 只得到一个私有工作区。

工具名固定为：

```text
list_files  read_file  search_files  write_file  edit_file
```

命名不带 artifact；第一版也不做 tar、上传下载和远端同步。

## 2. 项目里的最小实现

```text
taskId
  -> AgentRunner 创建 FileWorkspace(root/taskId)
  -> FileToolsPlugin 暴露五个 Schema
  -> Python Policy 检查字段、大小和 hash 格式
  -> FileWorkspace 检查路径/symlink/大小/CAS
  -> ToolResult 进入模型；脱敏参数和有界 diff 进入 Trace
```

Session resume 沿用 sessionId，但生成新 taskId，所以不会自动继承旧工作区。这是明确的第一版边界。

## 3. 代码阅读顺序

1. `agentd/app/plugins/files.py`：五个 Tool Schema；
2. `agentd/app/tools/files.py` 的 `_parts`、`_path`：路径边界；
3. `_read_bytes`、`_replace`：有界读取和原子写；
4. `write_file`、`edit_file`：SHA256 CAS 与唯一替换；
5. `agentd/app/redaction.py`：Trace/Session 正文摘要；
6. `agentd/tests/test_files.py`：最短可执行规格。

## 4. 必须掌握的基础知识

### 路径穿越

`../`、绝对路径、空组件和 symlink 都可能让“工作区内路径”指向外部。本项目限制 POSIX 相对路径最多 8 层/512 bytes，逐级 `lstat` 拒绝 symlink，再确认 resolve 后仍位于 task 根目录。

### 符号链接与 TOCTOU

只做字符串前缀检查挡不住 symlink。`O_NOFOLLOW` 可保护最终文件，逐级 `lstat` 保护父路径。教学实现仍存在检查与打开之间的极小竞态；生产实现可用 Linux `openat2(RESOLVE_BENEATH|RESOLVE_NO_SYMLINKS)` 或隔离进程进一步收紧。

### CAS

覆盖已有文件必须带当前 SHA256。若 A 读取后 B 已修改，A 的旧 hash 会失败，而不是无声覆盖 B。CAS 解决并发覆盖，不等于数据库事务。

### 原子替换

内容先写同目录 0600 临时文件、`fsync`，再 `os.replace`。同文件系统内 rename 是原子的，读者看到旧版或新版，不看到半个文件。

### Diff 与秘密

工具返回旧/新 hash 和最多 4096 字符 unified diff。常见 API Key/Bearer 会在 read/search/diff 输出中脱敏；Trace/Session 对 write content、edit oldText/newText 只保存 bytes 与 SHA256，不复制正文。这是模式脱敏，不是完整 DLP。

## 5. 为什么采用当前方案

- 普通文件比数据库和对象存储更容易读懂；
- taskId 目录天然演示多任务身份隔离；
- 256 KiB 上限足够诊断草案，避免模型制造大文件；
- 字面量搜索没有正则灾难性回溯；
- exact oldText + CAS 让 edit 行为可预测、可审计；
- 0700/0600 放在 WSL 原生文件系统，权限语义真实。

## 6. 没有采用的方案

- 通用 Bash `cat/sed/grep`：路径和命令面过大；
- tar/SFTP：涉及归档穿越、远端身份和传输配额；
- Session 共享目录：需要所有权和生命周期设计；
- 自动清理器：第一版任务量小，后台并发会增加复杂度；
- 向量库/RAG：与本地短文本草案无关。

## 7. 常见错误和本项目踩坑

- `Path.resolve()` 不是 symlink 策略，必须显式 `lstat`；
- `write_text()` 不能表达 CAS，也可能留下半写文件；
- 在 `/mnt/c` 上 `chmod` 成功不代表权限生效；
- 把写入正文原样放进 Trace，会让本地审计文件成为秘密副本；
- Diff 能帮助复核，但 Diff 本身也可能泄密，必须有界并脱敏。

## 8. 高频问题与回答思路

**Q：为什么 write_file 算安全写操作？**

它只写 task 草案目录，不写 Kubernetes、Linux Target 或仓库；“文件写成功”不代表外部变更已执行。

**Q：为什么既要 expectedSha256，又要 oldText 唯一？**

hash 保证编辑的是读过的版本；唯一出现保证替换位置没有歧义，两个约束解决不同问题。

**Q：工作区和 Session 是什么关系？**

Session 是可 resume 的事故上下文，Workspace 属于一次 Task。当前 resume 创建新 Workspace；若未来共享，必须增加显式归属、配额和清理策略。

## 9. 自己动手验证

```bash
uv run --project agentd --frozen python -m unittest agentd.tests.test_files
./hack/run-linux-agent-demo.sh
```

建议破坏实验：把 expectedSha256 改成 64 个零、创建指向外部的 symlink、尝试 `../escape`，三者都应失败。

## 10. 一分钟项目讲法

“我参考 Coding Agent 的文件能力，但没有开放宿主目录，而是每个 taskId 创建独立 0700 工作区。路径只接受相对 POSIX 形式，逐级拒绝 symlink 和逃逸；单文件 256 KiB。覆盖和编辑用 SHA256 做 CAS，oldText 必须唯一，写入先落同目录临时文件再原子 replace，并返回有界脱敏 diff。写入正文不会复制进 Trace/Session。它支持 Agent 整理诊断草案，但不等于获得外部系统写权限。”
