"""从固定上游缓存生成带许可/commit/hash的40篇运维文档快照。"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import subprocess
from pathlib import Path

RUNBOOKS = {
    "kubernetes": ["KubePodCrashLooping", "KubePodNotReady", "KubeContainerWaiting", "KubeNodeNotReady",
                   "KubeNodeReadinessFlapping", "KubeNodeUnreachable", "KubeDeploymentReplicasMismatch",
                   "KubeDeploymentGenerationMismatch", "KubeStatefulSetReplicasMismatch", "KubeJobFailed",
                   "KubeJobCompletion", "KubeHpaMaxedOut", "KubeHpaReplicasMismatch", "CPUThrottlingHigh",
                   "KubePersistentVolumeFillingUp", "KubePersistentVolumeErrors", "KubeMemoryOvercommit", "KubeCPUOvercommit"],
    "node": ["NodeFilesystemSpaceFillingUp", "NodeFilesystemAlmostOutOfSpace", "NodeFilesystemFilesFillingUp",
             "NodeFilesystemAlmostOutOfFiles", "NodeFileDescriptorLimit", "NodeHighNumberConntrackEntriesUsed"],
    "prometheus": ["PrometheusBadConfig", "PrometheusNotIngestingSamples", "PrometheusRuleFailures", "PrometheusRemoteWriteBehind"],
    "etcd": ["etcdNoLeader", "etcdInsufficientMembers", "etcdHighFsyncDurations", "etcdBackendQuotaLowSpace"],
}

PROJECT_DOCS = {
    "fixture-payments-memory": """# Payments memory incident fixture

## Scope
This is a synthetic sandboxd teaching scenario, not a production incident. In the fixed 2026-09-01 log snapshot, payments records OOM_KILLED during 00:15–00:30 UTC. A log label is evidence to verify, not proof of the underlying workload's actual Kubernetes termination reason.

## Diagnosis
Inspect the container last termination reason and exit status, Pod events, memory limits and memory usage over the same time window. Compare the observed peak with the configured limit. Check recent changes and whether the application has a leak or a temporary spike. Retrieve Kubernetes resource-management guidance before recommending a limit change.

## Evidence limits
The fixture contains no actual Pod status or memory metrics. It cannot prove a leak, a specific memory limit, or a successful recovery. Report the missing observations and propose verification. The demo does not apply resource changes.
""",
    "fixture-checkout-service": """# Checkout service incident fixture

## Scope
This is a synthetic sandboxd teaching scenario. Checkout logs upstream connection refused during 00:15–00:30 UTC; catalog logs readiness probe failed connection timeout. These simultaneous observations are hypotheses to investigate, not proof that catalog caused checkout's failure.

## Diagnosis
Check the destination Service, selectors and EndpointSlices. Compare the Service targetPort with the application's listening port and validate backend Pod readiness. Inspect logs and events from the relevant backend before testing connectivity. Check DNS only if name resolution is implicated by the evidence.

## Evidence limits
No live Service selector, endpoint or network-policy data is included in the replay. Ask for these observations before asserting a specific misconfiguration. Changes to workloads remain subject to the existing approval boundary.
""",
}


def sections(markdown: str) -> list[tuple[str, str]]:
    markdown = re.sub(r"\A---\n.*?\n---\n", "", markdown, flags=re.S)
    markdown = re.sub(r"{{[%<].*?[%>]}}", "", markdown, flags=re.S)
    result: list[tuple[str, str]] = []
    title, lines, fenced = "overview", [], False
    for line in markdown.splitlines():
        if line.lstrip().startswith(("```", "~~~")):
            fenced = not fenced
        match = re.match(r"^#{1,6}\s+(.+)", line) if not fenced else None
        if match:
            if "\n".join(lines).strip():
                result.append((title, "\n".join(lines).strip()))
            title, lines = match[1], []
        else:
            lines.append(line)
    if "\n".join(lines).strip():
        result.append((title, "\n".join(lines).strip()))
    return result


def chunks_for(doc: dict, text: str) -> list[dict]:
    chunks = []
    for section_number, (heading, body) in enumerate(sections(text), 1):
        # 先以段落分块；代码围栏内的空行不拆，保留命令与结果。
        blocks, block, fenced = [], [], False
        for line in body.splitlines():
            if line.lstrip().startswith(("```", "~~~")):
                fenced = not fenced
            if not line.strip() and not fenced:
                if block:
                    blocks.append("\n".join(block)); block = []
            else:
                block.append(line)
        if block:
            blocks.append("\n".join(block))
        parts, current = [], ""
        for block in blocks:
            if current and len(current) + len(block) > 1800:
                parts.append(current); current = ""
            current = current + "\n\n" + block if current else block
        if current:
            parts.append(current)
        for part_number, part in enumerate(parts, 1):
            if len(part) > 16384:
                raise ValueError("单个文档块过大，需要人工拆分: " + doc["docId"])
            section_id = f"{doc['docId']}:s{section_number:03}"
            chunks.append({"chunkId": f"{section_id}:p{part_number:02}", "docId": doc["docId"],
                           "title": doc["title"] + " / " + heading,
                           "text": part, "source": doc["url"], "corpusVersion": "ops-v1",
                           "sectionId": section_id, "component": doc["component"], "sourceRevision": doc["revision"]})
    return chunks


def build(cache: Path, output: Path) -> dict:
    output.mkdir(parents=True, exist_ok=True)
    sources = output / "sources"; sources.mkdir(exist_ok=True)
    licenses = output / "licenses"; licenses.mkdir(exist_ok=True)
    runbooks = cache / "runbooks"
    revision = subprocess.check_output(["git", "-C", str(runbooks), "rev-parse", "HEAD"], text=True).strip()
    docs, chunks = [], []
    def add(doc_id: str, component: str, raw: bytes, url: str, rev: str, license_name: str, title: str):
        doc = {"docId": doc_id, "component": component, "url": url, "revision": rev,
               "license": license_name, "title": title, "sha256": hashlib.sha256(raw).hexdigest(),
               "file": f"sources/{doc_id}.md"}
        (sources / f"{doc_id}.md").write_bytes(raw)
        docs.append(doc); chunks.extend(chunks_for(doc, raw.decode()))
    for component, names in RUNBOOKS.items():
        for name in names:
            path = f"content/runbooks/{component}/{name}.md"
            add("runbook-" + name, component, (runbooks / path).read_bytes(),
                f"https://github.com/prometheus-operator/runbooks/blob/{revision}/{path}", revision, "Apache-2.0", name)
    for item in json.loads((cache / "kubernetes/sources.json").read_text()):
        if item["file"] == "LICENSE":
            continue
        stem = Path(item["file"]).stem
        add("k8s-" + stem, "kubernetes", (cache / "kubernetes" / item["file"]).read_bytes(),
            item["url"].replace("raw.githubusercontent.com/kubernetes/website/", "github.com/kubernetes/website/blob/"),
            item["revision"], "CC-BY-4.0", stem.replace("-", " "))
    for name, text in PROJECT_DOCS.items():
        add(name, "project-fixture", text.encode(), f"synthetic://sandboxd/{name}", "fixture-v1", "project", name)
    if len(docs) != 40:
        raise ValueError(f"预期40篇，实际{len(docs)}")
    shutil.copyfile(runbooks / "LICENSE", licenses / "prometheus-runbooks-APACHE-2.0.txt")
    shutil.copyfile(cache / "kubernetes/LICENSE", licenses / "kubernetes-CC-BY-4.0.txt")
    manifest = {"version": "ops-v1", "documentCount": len(docs), "chunkCount": len(chunks),
                "processing": "raw snapshot retained; front matter/shortcodes stripped and heading/paragraph chunked v1",
                "documents": docs}
    (output / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
    (output / "corpus.jsonl").write_text("".join(json.dumps(c, ensure_ascii=False) + "\n" for c in chunks))
    return {"documentCount": len(docs), "chunkCount": len(chunks), "runbooksRevision": revision}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--cache", type=Path, default=Path(".cache/phase8"))
    parser.add_argument("--output", type=Path, default=Path("agentd/retrieval/data/ops-v1"))
    args = parser.parse_args()
    print(json.dumps(build(args.cache, args.output), indent=2))
