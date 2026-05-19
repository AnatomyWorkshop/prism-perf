# prism-perf

Performance impossibility detection for microservice architectures.

Given a service topology, resource constraints, and an SLA target, prism-perf proves whether your target is mathematically achievable — or gives you the exact reason it's not.

## What it does

```
$ python prism_perf.py check examples/payment_chain.yaml

VERDICT: INFEASIBLE

BINDING CONSTRAINTS:

  1. Serial Latency Sum [worst-case bound]
     Chain: api_gateway → auth_service → payment_gateway → db_write
     Lower bound: 541.5ms
     Target: 100.0ms
     Gap: 441.5ms (5.4× over target)

  2. Little's Law [exact bound]
     Required concurrency: 10000 QPS × 200.0ms = 2000
     Available: 20 connections
     Max achievable: 100 QPS

RECOMMENDATIONS:
  - Make payment_gateway async (removes 500ms from critical path)
  - Increase connection pool to ≥ 2000
```

Observability tools tell you what IS. Load tests tell you what HAPPENS. prism-perf tells you what CANNOT BE.

## Quick start

```bash
# Scan your project — reads docker-compose.yml, k8s/, openapi.yaml automatically
python prism_perf.py scan . --output topology.yaml

# With AI latency estimation (reads source code, estimates p50/p99)
# Requires ANTHROPIC_API_KEY
python prism_perf.py scan . --ai --output topology.yaml

# Or generate topology from OpenTelemetry trace (latencies auto-measured)
python prism_perf.py infer trace.json --output topology.yaml

# Check feasibility
python prism_perf.py check topology.yaml
```

## Constraint engine

Four mathematical bounds, each with a precise proof when violated:

| Constraint | What it catches | Bound type |
|---|---|---|
| **Serial Latency Sum** | Chain p99 ≥ sum of component p99s | Worst-case (sound: if we say impossible, it is) |
| **Little's Law** | Required concurrency = throughput × latency | Exact |
| **Amdahl's Law** | Horizontal scaling saturation per service | Exact |
| **M/M/c Queue Stability** | λ ≥ cμ → unbounded queue growth | Exact |

Planned: CAP theorem detection, hardware physical limits, bandwidth bounds.

## Topology format

```yaml
topology:
  name: "my-service"
  services:
    - name: api_gateway
      type: sync
      latency_p50: 2ms
      latency_p99: 5ms
    - name: database
      type: sync
      latency_per_op: 5ms
      connections: 20
      parallelism: 4
      serial_fraction: 0.15

  chain:
    - api_gateway -> database

  resources:
    connection_pool: 20
    network_rtt_internal: 0.5ms

target:
  latency_p99: 50ms
  throughput: 5000 qps
```

Don't want to write YAML from scratch? Use `prism-perf infer` with an OpenTelemetry JSON trace export — it extracts services, latencies, and call chains automatically.

## How it works

No machine learning. No heuristics. Just well-known mathematical bounds (Little's Law, Amdahl's Law, queuing theory) applied to your specific deployment topology via constraint propagation.

When prism-perf says INFEASIBLE, it's provably true — not a guess, not a prediction, not a statistical estimate. The evidence shows exactly which law is violated and by how much.

When it says FEASIBLE, it means no known bound is violated. Your system might still fail for reasons outside the model (GC pauses, kernel scheduling, network jitter). The tool gives hard lower bounds, not performance predictions.

## GitHub Action

Add automatic performance feasibility checks to your PRs. Copy this into `.github/workflows/perf-check.yml`:

```yaml
name: Performance Feasibility Check

on:
  pull_request:
    paths:
      - '**/topology.yaml'
      - 'infra/**/*.yaml'

jobs:
  check:
    runs-on: ubuntu-latest
    permissions:
      pull-requests: write
    steps:
      - uses: actions/checkout@v4
      - uses: AnatomyWorkshop/prism-perf@v0.1.0
        with:
          topology-path: 'infra/topology.yaml'
          comment: 'true'
          fail-on-infeasible: 'false'
```

When a PR modifies your topology, prism-perf comments the verdict directly on the PR:

- **FEASIBLE** — no known bound violated, shows tightest constraint and margin
- **INFEASIBLE** — mathematical proof of impossibility with exact binding constraint
- **UNKNOWN** — insufficient data, suggests what to measure next

Set `fail-on-infeasible: 'true'` to block merge on infeasible targets.

## Run tests

```bash
python tests/test_solver.py
```

## License

MIT
