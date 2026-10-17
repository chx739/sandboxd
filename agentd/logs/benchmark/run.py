"""固定只读 Benchmark + 同期节点 CPU/JVM 采样；不发模型请求。"""
from __future__ import annotations
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import time
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[3]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True, help='new JSON report; sibling CSV/log also must be new')
    parser.add_argument('--benchmark', type=Path, default=ROOT / '.cache/ops-benchmark-venv/bin/opensearch-benchmark')
    args = parser.parse_args()
    output = args.output.resolve()
    csv, log = output.with_suffix('.csv'), output.with_suffix('.log')
    if any(p.exists() for p in [output, csv, log]):
        parser.error('不能覆盖已有结果')
    output.parent.mkdir(parents=True, exist_ok=True)
    workload = ROOT / 'agentd/logs/benchmark/workload.json'
    command = [str(args.benchmark.resolve()), 'run', '--pipeline=benchmark-only',
        '--workload-path=' + str(workload), '--target-hosts=127.0.0.1:9201',
        '--distribution-version=2.19.6', '--offline', '--results-format=csv', '--latency-percentiles=50,95,99',
        '--results-file=' + str(csv), '--client-options=timeout:10', '--on-error=abort']
    environment = dict(os.environ)
    environment.setdefault('BENCHMARK_HOME', str(Path.home() / '.local/share/sandboxd/benchmark'))
    started = time.monotonic()
    report = {'kind': 'real-local-query-benchmark-with-resource-samples', 'command': command,
        'workloadSha256': hashlib.sha256(workload.read_bytes()).hexdigest(),
        'sampleIntervalSeconds': 1, 'deadlineSeconds': 180, 'samples': [], 'status': 'running',
        'scope': 'OpenSearch node process CPU percent and JVM heap/nonheap bytes; not container RSS or host-wide CPU. Sampling adds monitoring overhead. Tiny warmed synthetic workload, not production capacity.'}
    def save():
        output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
    save()
    with log.open('x') as handle:
        process = subprocess.Popen(command, cwd=ROOT, env=environment, stdout=handle, stderr=subprocess.STDOUT)
        try:
            while process.poll() is None:
                elapsed = time.monotonic() - started
                if elapsed > 180:
                    raise TimeoutError('bounded benchmark deadline')
                try:
                    with urlopen('http://127.0.0.1:9201/_nodes/stats/jvm,process', timeout=2) as response:
                        nodes = json.load(response)['nodes']
                    for node in nodes.values():
                        report['samples'].append({'elapsedSeconds': round(elapsed, 3),
                            'timestamp': node['timestamp'], 'cpuPercent': node['process']['cpu']['percent'],
                            'heapUsedBytes': node['jvm']['mem']['heap_used_in_bytes'],
                            'heapMaxBytes': node['jvm']['mem']['heap_max_in_bytes'],
                            'nonHeapUsedBytes': node['jvm']['mem']['non_heap_used_in_bytes']})
                except Exception as exc:
                    report.setdefault('samplingErrors', []).append({'elapsedSeconds': round(elapsed, 3), 'type': type(exc).__name__})
                save()
                time.sleep(1)
            report['exitCode'] = process.returncode
            report['status'] = 'completed' if process.returncode == 0 else 'failed'
        except BaseException as exc:
            process.terminate()
            try: process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill(); process.wait()
            report['status'], report['errorType'] = 'stopped', type(exc).__name__
            raise
        finally:
            report['wallSeconds'] = round(time.monotonic() - started, 3)
            samples = report['samples']
            report['summary'] = {'sampleCount': len(samples), **{
                'max' + key[0].upper() + key[1:]: max(s[key] for s in samples) if samples else None
                for key in ['cpuPercent', 'heapUsedBytes', 'nonHeapUsedBytes']}}
            save()
    print(json.dumps({k: v for k, v in report.items() if k != 'samples'}, ensure_ascii=False, indent=2))
    raise SystemExit(0 if report['status'] == 'completed' and report['samples'] and not report.get('samplingErrors') else 1)


if __name__ == '__main__':
    main()
