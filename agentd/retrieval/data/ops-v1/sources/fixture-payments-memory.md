# Payments memory incident fixture

## Scope
This is a synthetic sandboxd teaching scenario, not a production incident. In the fixed 2026-09-01 log snapshot, payments records OOM_KILLED during 00:15–00:30 UTC. A log label is evidence to verify, not proof of the underlying workload's actual Kubernetes termination reason.

## Diagnosis
Inspect the container last termination reason and exit status, Pod events, memory limits and memory usage over the same time window. Compare the observed peak with the configured limit. Check recent changes and whether the application has a leak or a temporary spike. Retrieve Kubernetes resource-management guidance before recommending a limit change.

## Evidence limits
The fixture contains no actual Pod status or memory metrics. It cannot prove a leak, a specific memory limit, or a successful recovery. Report the missing observations and propose verification. The demo does not apply resource changes.
