# Checkout service incident fixture

## Scope
This is a synthetic sandboxd teaching scenario. Checkout logs upstream connection refused during 00:15–00:30 UTC; catalog logs readiness probe failed connection timeout. These simultaneous observations are hypotheses to investigate, not proof that catalog caused checkout's failure.

## Diagnosis
Check the destination Service, selectors and EndpointSlices. Compare the Service targetPort with the application's listening port and validate backend Pod readiness. Inspect logs and events from the relevant backend before testing connectivity. Check DNS only if name resolution is implicated by the evidence.

## Evidence limits
No live Service selector, endpoint or network-policy data is included in the replay. Ask for these observations before asserting a specific misconfiguration. Changes to workloads remain subject to the existing approval boundary.
