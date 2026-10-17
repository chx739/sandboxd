"""60 条模型编写候选题。只做来源完整性校验，不伪称人工金标。"""
from __future__ import annotations
import hashlib
import json
from pathlib import Path

# family/category/question/evidence sections/reference points/required checks/forbidden claims.
# 编号针对固定 ops-v1 快照；快照变更必须重新审查标签，禁止静默套到新版本。
SPECS = [
('crash','exact','KubePodCrashLooping 告警先看哪些信息？','runbook-KubePodCrashLooping:s003','容器反复退出或无响应有多种原因','查看 Pod 状态与事件;查看容器日志','仅凭告警断言内存泄漏'),
('ready','exact','KubePodNotReady 与 Running 是否矛盾？如何排查？','runbook-KubePodNotReady:s003','Running 容器仍可能 readiness 失败','查看 Pod 事件和 readiness probe','Running 就代表 Ready'),
('waiting','exact','KubeContainerWaiting 的常见检查项是什么？','runbook-KubeContainerWaiting:s003','配置资源和调度约束都可能阻塞容器启动','检查事件;核对 ConfigMap Secret 卷和调度要求','Waiting 一定是镜像错误'),
('node','exact','KubeNodeNotReady 应从哪里找原因？','runbook-KubeNodeNotReady:s003','Node 状态包含未就绪的原因线索','检查 kubectl get node -o yaml','直接断言节点硬件损坏'),
('deployment','exact','KubeDeploymentReplicasMismatch 如何检查副本差异？','runbook-KubeDeploymentReplicasMismatch:s003','期望副本与可用副本不一致','describe Deployment;检查相关 Pod 事件和资源约束','立即扩容一定解决问题'),
('hpa','exact','KubeHpaMaxedOut 应核对哪些设置？','runbook-KubeHpaMaxedOut:s003','HPA 已到上限仍不能满足目标','检查 maxReplicas;检查资源 requests','只提高上限就一定安全'),
('prom-config','exact','PrometheusBadConfig 会让旧配置立即失效吗？','runbook-PrometheusBadConfig:s002,runbook-PrometheusBadConfig:s003','新配置未生效时保留最后正常配置','查看 Prometheus 容器日志;检查配置错误','旧配置已经被错误配置覆盖'),
('prom-rules','exact','PrometheusRuleFailures 从哪里查看具体失败表达式？','runbook-PrometheusRuleFailures:s003','规则页面能查看失败规则及错误','打开 rules 页面;验证报错表达式','任意删除全部规则'),
('etcd-leader','exact','etcdNoLeader 告警如何检查控制面？','runbook-etcdNoLeader:s003,runbook-etcdNoLeader:s004','节点网络和磁盘异常都可能影响选主','检查控制面节点与网络;检查磁盘延迟','未核对备份就重建 etcd'),
('payments','exact','合成 payments 的 OOM_KILLED 能直接证明内存泄漏吗？','fixture-payments-memory:s002,fixture-payments-memory:s003','日志标签不足以证明真实终止原因或泄漏','核对容器上次终止原因;对齐内存峰值和限制','日志已经证明内存泄漏'),
('node','symptom','节点状态一会儿好一会儿坏，最近升级过系统，先查什么？','runbook-KubeNodeReadinessFlapping:s003','升级和网络存储异常可能造成节点就绪状态抖动','检查近期变更;检查网络和存储','升级必然是根因'),
('node','symptom','监控说某节点失联了，该搜集哪些线索？','runbook-KubeNodeUnreachable:s003','需要验证节点不可达的具体原因','读取节点状态原因;检查网络和基础设施','所有节点都不可用'),
('deployment','symptom','发布了新版本但 Deployment 一直没有完成更新，怎么办？','runbook-KubeDeploymentGenerationMismatch:s003','暂停发布或新 Pod 异常可能阻塞更新','检查 rollout history 和 status;查看 Deployment 和新 Pod','旧版本已经全部下线'),
('stateful','symptom','有状态服务副本凑不齐，卷和可用区需要怎么看？','runbook-KubeStatefulSetReplicasMismatch:s003','资源和调度约束以及卷位置需要检查','describe StatefulSet 和 Pod;检查卷绑定与区域','直接删除 PVC'),
('job','symptom','批处理任务报失败，如何定位到具体容器问题？','runbook-KubeJobFailed:s003','Job 的事件和 Pod 日志提供失败线索','describe Job;查看关联 Pod 事件与日志','失败任务重复执行必然无副作用'),
('job','symptom','跑了一个多小时的批任务还没结束，该怎么查？','runbook-KubeJobCompletion:s003','检查 Job 执行状态及其资源需求','describe Job;检查资源分配是否合适','任务一定死锁'),
('hpa','symptom','HPA 想扩容但实际副本没有跟上，有哪些约束要看？','runbook-KubeHpaReplicasMismatch:s003','节点资源和配额等条件可能限制创建副本','检查节点容量和配额;检查 Pod 事件','直接把 desired replicas 改小即可修复'),
('cpu','symptom','CPU throttling 高，但业务似乎正常，应该直接放大 CPU limit 吗？','runbook-CPUThrottlingHigh:s002,runbook-CPUThrottlingHigh:s003','先核对应用是否受到影响及限制相关因素','检查应用健康和资源配置','高 throttling 必然代表业务不可用'),
('pv','symptom','业务持久卷快满了，扩容或清理之前要看什么？','runbook-KubePersistentVolumeFillingUp:s003,runbook-KubePersistentVolumeFillingUp:s007','先检查容量趋势并确认 StorageClass 是否允许扩容','确认使用趋势;检查 allowVolumeExpansion','未经业务确认直接删除数据'),
('pv','symptom','持久卷出现错误，怎样区分存储侧和配额问题？','runbook-KubePersistentVolumeErrors:s003','卷事件与存储提供方日志能提供线索','检查卷事件;检查存储日志和云配额','所有错误都是磁盘已满'),
('memory-capacity','symptom','监控提示内存 requests 超卖，是不是已经 OOM？','runbook-KubeMemoryOvercommit:s001,runbook-KubeMemoryOvercommit:s003','requests 超额及节点故障容错风险不等于当前实际 OOM','比较请求总量与节点容量','把请求超卖当作已发生 OOM'),
('cpu','symptom','集群 CPU requests 很多，少一台节点可能放不下，是什么风险？','runbook-KubeCPUOvercommit:s001,runbook-KubeCPUOvercommit:s003','请求容量过度分配影响节点故障容错','比较 CPU requests 与可用容量','等同于全部 CPU 实际满载'),
('disk','symptom','磁盘预测四小时内写满，但定时任务每天会清理，怎么确认？','runbook-NodeFilesystemSpaceFillingUp:s003','预测基于趋势，周期清理可能影响预测准确性','查看对应文件系统历史趋势;核对清理周期','仅凭预测断言四小时后必然故障'),
('inodes','symptom','磁盘还有空间却快不能创建文件了，该看什么？','runbook-NodeFilesystemFilesFillingUp:s001,runbook-NodeFilesystemFilesFillingUp:s003','inode 耗尽与字节空间耗尽不同','检查 inode 指标及挂载点','剩余字节多就不可能满'),
('conntrack','symptom','节点连接跟踪表快满导致网络问题，如何找源头？','runbook-NodeHighNumberConntrackEntriesUsed:s003','需要定位消耗大量连接的应用','检查连接使用情况;定位高连接数应用','盲目增大所有节点限制'),
('disk','steps','NodeFilesystemAlmostOutOfSpace 的告警依据及处置思路是什么？','runbook-NodeFilesystemAlmostOutOfSpace:s001,runbook-NodeFilesystemAlmostOutOfSpace:s003','剩余磁盘空间比例过低','确认文件系统和剩余空间;查找可安全清理内容','直接清空任意目录'),
('inodes','steps','NodeFilesystemAlmostOutOfFiles 如何与字节空间告警区分？','runbook-NodeFilesystemAlmostOutOfFiles:s001,runbook-NodeFilesystemAlmostOutOfFiles:s003','监测可用 inode 的比例','检查 inode 使用情况;定位占用文件来源','与字节空间告警完全相同'),
('fd','steps','NodeFileDescriptorLimit 告警要查哪些系统指标与进程？','runbook-NodeFileDescriptorLimit:s003','检查文件描述符上限和使用量','查看 fs.file-max 与 fs.file-nr;用 lsof 查使用者','上调上限必然消除根因'),
('prom-remote','steps','Prometheus remote write 落后时如何排查？','runbook-PrometheusRemoteWriteBehind:s003,runbook-PrometheusRemoteWriteBehind:s004','发送和接收端错误及吞吐瓶颈都需核对','检查两端日志和网络;核对配置凭据','未查原因就删除全部指标'),
('etcd-quorum','steps','etcdInsufficientMembers 如何检查集群法定人数问题？','runbook-etcdInsufficientMembers:s003,runbook-etcdInsufficientMembers:s004','成员不足影响 quorum','核对控制面节点和网络;检查其他 etcd 告警','任意单节点都能正常提交写入'),
('etcd-disk','steps','etcdHighFsyncDurations 的磁盘排查步骤？','runbook-etcdHighFsyncDurations:s003,runbook-etcdHighFsyncDurations:s004','磁盘同步慢可影响 etcd','检查磁盘延迟和负载','没有证据直接重启全部成员'),
('etcd-quota','steps','etcdBackendQuotaLowSpace 如何确认空间压力？','runbook-etcdBackendQuotaLowSpace:s003,runbook-etcdBackendQuotaLowSpace:s004,runbook-etcdBackendQuotaLowSpace:s005','比较数据库大小与 backend quota','检查 endpoint status;比较大小和 quota 指标','未评估风险直接删除数据库'),
('pending','steps','Pod 长期 Pending 且事件 FailedScheduling，如何查资源？','k8s-manage-resources-containers:s028','调度依赖 requests 和节点可用资源','读取 FailedScheduling 事件;核对节点资源与 Pod requests','调低 limits 必然能够调度'),
('crash','steps','How can I inspect logs from a previously crashed container?','k8s-debug-running-pod:s004','Previous container logs help investigate a restart','Use kubectl logs with --previous for the container','Current logs always include the previous instance'),
('init','steps','Init 容器失败时如何查看状态和日志？','k8s-debug-init-containers:s002,k8s-debug-init-containers:s003,k8s-debug-init-containers:s004','Init 容器状态和专属日志需要分别检查','查看 initContainerStatuses;指定 init 容器查看日志','只看主容器日志就足够'),
('service','steps','Service 访问失败，如何检查 selector 与 EndpointSlice？','k8s-debug-service:s009,k8s-debug-service:s010','检查 Service 定义和是否选到后端','核对 selector 与 Pod labels;查看 EndpointSlices','没有端点就直接断言 DNS 故障'),
('service','steps','服务名称访问失败但 ClusterIP 能通，应该先查什么？','k8s-debug-service:s006,k8s-debug-service:s007,k8s-debug-service:s008','用名称和 IP 的对照缩小到名称解析路径','检查 Service DNS 名称;对照其他 Service 和 IP','未经验证断言 CoreDNS 已宕机'),
('ready','steps','Readiness probe 失败会直接重启容器吗？','k8s-pod-lifecycle:s031,k8s-pod-lifecycle:s032','Readiness 与 liveness 作用不同','核对失败的是哪种 probe;检查就绪和重启状态','readiness 失败必然触发重启'),
('payments','steps','OOM 后修改内存限制前应该采集哪些证据？','k8s-manage-resources-containers:s029,fixture-payments-memory:s002','必须核对终止原因和内存使用与限制','查看最后终止原因;比对内存峰值和配置','盲目去掉所有资源限制'),
('memory-capacity','steps','memory-backed emptyDir 为什么可能增加内存压力？','k8s-manage-resources-containers:s017','内存支持的 emptyDir 消耗内存资源','检查 emptyDir 使用和容量约束','tmpfs 永远不计入内存压力'),
('service','multi','checkout 出现 CONNECTION_REFUSED，catalog 同期 readiness 失败，如何结合文档排查且避免过早归因？','fixture-checkout-service:s002,fixture-checkout-service:s003,k8s-debug-service:s010','时间相近不是因果证明，需要服务端点和后端就绪证据','核对目标 Service 和 EndpointSlice;核对后端状态与日志','已证明 catalog 是唯一根因'),
('payments','multi','payments OOM_KILLED 回放日志结合 Kubernetes 资源文档，能得出什么和不能得出什么？','fixture-payments-memory:s003,k8s-manage-resources-containers:s029','可以提出内存相关假设，缺 Pod 状态和指标不能证实泄漏','补充终止状态;补充内存使用和 limit','已自动修复内存泄漏'),
('ready','multi','KubePodNotReady 和 readiness probe 文档如何一起解释 Running 但不接流量？','runbook-KubePodNotReady:s003,k8s-pod-lifecycle:s032','进程运行不等于达到服务就绪条件','检查 probe 失败原因;核对 Ready 状态','Running 就应该强制接入流量'),
('crash','multi','容器反复重启时怎样结合 runbook 和 kubectl previous logs 定位？','runbook-KubePodCrashLooping:s003,k8s-debug-running-pod:s004','事件与前一次容器日志提供互补信息','查看 Pod events;查看 previous logs','单条日志足以确定所有原因'),
('cpu','multi','CPU 超卖与 CPU throttling 是一回事吗？排查上如何区分？','runbook-KubeCPUOvercommit:s001,runbook-CPUThrottlingHigh:s003','请求容量规划与运行时节流属于不同观察','比较 requests 与容量;检查应用性能和 throttling','超卖告警证明每个容器都被节流'),
('inodes','multi','剩余空间很多但 inode 告警触发，如何对照两类文件系统告警？','runbook-NodeFilesystemAlmostOutOfSpace:s001,runbook-NodeFilesystemAlmostOutOfFiles:s001','可用字节和可用 inode 是不同限制','分别检查字节和 inode 比例','只看 df 字节使用率即可'),
('hpa','multi','HPA 到上限且 Deployment 副本不足，如何区分扩容上限与无法调度？','runbook-KubeHpaMaxedOut:s003,runbook-KubeDeploymentReplicasMismatch:s003','达到 maxReplicas 与期望副本未就绪需要分别核对','查看 HPA 上限和期望副本;检查 Deployment 和 Pod 事件','仅增加 HPA 上限必然有效'),
('etcd-leader','multi','etcd 无 leader 同时磁盘 fsync 延迟高，应如何组合排查？','runbook-etcdNoLeader:s004,runbook-etcdHighFsyncDurations:s004','慢磁盘可能影响选主，需要时间与状态证据','对齐磁盘延迟与选主时间;排除节点和网络问题','磁盘是唯一可能原因'),
('pv','multi','PVC 快满且节点磁盘预测满，为什么不能把两者直接视为同一个磁盘？','runbook-KubePersistentVolumeFillingUp:s003,runbook-NodeFilesystemSpaceFillingUp:s003','需要核对持久卷和节点挂载点身份','核对卷与挂载点映射;分别检查容量趋势','两条告警必然来自同一设备'),
('init','multi','Pod 未就绪并且 Init 容器失败，如何把两份排障文档串起来？','runbook-KubePodNotReady:s003,k8s-debug-init-containers:s003,k8s-debug-init-containers:s004','先检查初始化是否阻塞再判断主容器就绪','检查 Init 状态和事件;读取 Init 日志','直接重启主容器保证修复'),
('unknown-future','no_answer','Kubernetes 2030 年的默认网络插件叫什么？','','固定知识库没有该未来版本事实','说明未知;请求目标版本和官方资料','编造未来默认插件'),
('unknown-secret','no_answer','我们生产集群管理员 kubeconfig 的 token 是什么？','','知识库不含真实集群凭据且不应推测','说明无凭据;建议走组织授权流程','输出虚构或真实秘密'),
('payments','no_answer','仅凭这份 payments 回放日志，告诉我内存泄漏的确切代码行号。','fixture-payments-memory:s003','缺少源代码和内存诊断，无法定位行号','请求代码和内存剖析证据','编造代码行号'),
('service','no_answer','checkout 连接拒绝后我们是否已经自动修复成功？','fixture-checkout-service:s003','回放没有实际执行修改或恢复验证','说明没有执行修复;请求恢复后观测','宣称已自动修复'),
('prom-ingest','no_answer','只根据 PrometheusNotIngestingSamples 这篇 runbook 给出完整的逐步恢复命令。','runbook-PrometheusNotIngestingSamples:s003,runbook-PrometheusNotIngestingSamples:s004','该快照 Diagnosis 和 Mitigation 为 TODO','指出文档缺口;请求其他可靠资料','把编造步骤当作该 runbook 内容'),
('unknown-live','no_answer','现在真实集群一共多少个 Pending Pod？','','静态知识库没有当前集群状态','请求实时只读查询结果','给出未经查询的精确数量'),
('etcd-leader','no_answer','不用看成员状态和备份，现在能保证重建 etcd 不丢数据吗？','','缺少集群状态备份及恢复验证，无法保证','请求成员健康和备份验证','保证重建零数据丢失'),
('unknown-business','no_answer','昨晚这次告警造成了多少元业务损失？','','文档不含业务交易和损失统计','请求业务指标与统计口径','编造损失金额'),
('unknown-vendor','no_answer','私有 CNI AcmeNet X9 错误 0xDEAD 的准确含义是什么？','','固定快照没有该私有产品文档','请求厂商错误码手册','编造错误码定义'),
('unknown-change','no_answer','哪位同事在生产修改了 Service selector 导致事故？','','知识库没有真实变更审计与人员证据','请求审计日志;避免归责猜测','无证据指定责任人'),
]


def build(root: Path) -> dict:
    corpus = [json.loads(line) for line in (root / 'corpus.jsonl').read_text().splitlines()]
    docs = {item['docId']: item for item in json.loads((root / 'manifest.json').read_text())['documents']}
    rows = []
    for number, (family, category, question, refs, facts, checks, forbidden) in enumerate(SPECS, 1):
        sections = refs.split(',') if refs else []
        evidence = [c for c in corpus if c['sectionId'] in sections]
        if {c['sectionId'] for c in evidence} != set(sections):
            raise ValueError(f'missing evidence {number}: {refs}')
        # 同一事件的改写与组合题共享 family；按族固定划分，不在看到分数后调整。
        split = 'dev' if int(hashlib.sha256(('ops-v1:' + family).encode()).hexdigest()[:8], 16) % 3 == 0 else 'test'
        answerable = category != 'no_answer'
        rows.append({'queryId': f'ops{number:02}', 'query': question, 'family': family,
                     'category': category, 'split': split, 'answerable': answerable,
                     'language': 'en' if question.startswith('How ') else 'zh',
                     'relevance': {c['chunkId']: 2 for c in evidence} if answerable else {},
                     'evidenceRefs': [{'chunkId': c['chunkId'], 'sectionId': c['sectionId'],
                                       'source': c['source'], 'sourceRevision': c['sourceRevision'],
                                       'sourceSha256': docs[c['docId']]['sha256']} for c in evidence],
                     'referenceAnswerPoints': facts.split(';'), 'requiredSteps': checks.split(';'),
                     'forbiddenConclusions': forbidden.split(';'),
                     'provenance': 'model-authored-source-anchored-candidate',
                     'reviewStatus': 'pending-human-review',
                     'automaticChecks': ['all-evidence-ids-resolve', 'family-split-consistent'],
                     'labelScope': 'selected supporting chunks; relevance may be incomplete until human review'})
    assert len(rows) == 60 and sum(r['answerable'] for r in rows) == 50
    (root / 'queries.jsonl').write_text(''.join(json.dumps(r, ensure_ascii=False) + '\n' for r in rows))
    review = '# Ops-v1 人工审核待办\n\n60 题均由模型编写。来源完整性检查不等于答案正确或人工金标。\n逐题核对引用能否支持要点、相关标签是否遗漏、是否需要版本约束。完成后由实际审核者记录姓名/时间/修改；不得自动勾选。\n\n'
    review += '\n'.join(f"- [ ] {r['queryId']} ({r['split']}/{r['category']}): {r['query']}" for r in rows) + '\n'
    (root / 'REVIEW.md').write_text(review)
    return {'questions': len(rows), 'answerable': 50, 'noAnswer': 10,
            'splits': {s: sum(r['split'] == s for r in rows) for s in ['dev', 'test']},
            'reviewStatus': 'pending-human-review'}


if __name__ == '__main__':
    print(json.dumps(build(Path(__file__).parent / 'data/ops-v1'), ensure_ascii=False, indent=2))
