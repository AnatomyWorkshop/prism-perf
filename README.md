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

CANONICAL ARCHITECTURE PATTERNS:

  1. Saga (Distributed Transaction)
     Why: Payment/shipping in synchronous path — Saga pattern removes them from critical path
     Model: sum(step_p99) but async — not on request critical path
     Use when: Multi-service transactions, payment flows

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
python prism_perf.py scan . --ai --output topology.yaml

# Or generate topology from OpenTelemetry trace (latencies auto-measured)
python prism_perf.py infer trace.json --output topology.yaml

# Check feasibility
python prism_perf.py check topology.yaml

# With AI optimization advice
python prism_perf.py check topology.yaml --advise

# With traffic forecast as throughput target
python prism_perf.py check topology.yaml --traffic traffic.csv
```

## Commands

### `check` — Feasibility analysis

```bash
python prism_perf.py check <topology.yaml> [--advise] [--traffic <data.csv>]
```

- `--advise` — call an AI model to generate ranked optimization fixes (requires API key)
- `--traffic <csv>` — use a traffic forecast as the throughput target instead of the YAML value

### `scan` — Auto-detect topology from project files

```bash
python prism_perf.py scan [dir] [--output <path>] [--ai]
```

Reads `docker-compose.yml`, Kubernetes manifests, and `openapi.yaml` to generate a topology YAML. Use `--ai` to estimate service latencies from source code (requires API key).

### `infer` — Generate topology from OTel trace

```bash
python prism_perf.py infer <trace.json> [--output <path>] [--preserve-target]
```

Extracts service names, p50/p99 latencies, and call chains from an OpenTelemetry JSON trace export. Use `--preserve-target` to keep your existing SLA targets when refreshing from a new trace.

### `forecast` — Traffic peak prediction

```bash
python prism_perf.py forecast <traffic.csv> [--days 30]
```

Fits a linear trend to historical QPS data and forecasts the p99 peak for the next N days. Output includes growth rate, confidence, and the recommended throughput target.

```bash
# Generate sample data to try it
python prism_perf.py sample-traffic
python prism_perf.py forecast sample_traffic.csv
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

## Architecture pattern library

When a topology is INFEASIBLE, prism-perf matches it to canonical patterns and explains the fix:

- **Saga** — payment/shipping in synchronous path → move to async distributed transaction
- **Async Fan-out** — high-latency services in chain → remove from p99 calculation
- **CQRS** — deep call chains → separate read/write paths
- **Bulkhead** — connection pool exhaustion → isolate pools per downstream
- **Cache-Aside** — high concurrency demand → reduce DB load

## AI optimization advice

With `--advise`, prism-perf calls an AI model to generate ranked fixes with estimated impact, effort, and tradeoffs:

```
AI OPTIMIZATION ADVICE
============================================================

ARCHITECTURAL PATTERN: Saga

RANKED FIXES (by impact/effort):

  1. Move payment_gateway to async Saga pattern
     Impact:    Removes 500ms from synchronous critical path
     Effort:    medium
     Tradeoff:  Eventual consistency; requires compensation logic

  2. Add Redis cache in front of auth_service
     Impact:    Reduces auth p99 from 30ms to ~2ms for cached tokens
     Effort:    low
     Tradeoff:  Token revocation delay up to cache TTL
```

Configure the AI backend via environment variables:

```bash
# Option 1: Deepseek (recommended — fast, cheap, good at structured output)
export DEEPSEEK_API_KEY=sk-...

# Option 2: Anthropic
export ANTHROPIC_API_KEY=sk-ant-...

# Option 3: Any OpenAI-compatible endpoint
export PRISM_AI_BASE_URL=https://your-endpoint.com
export PRISM_AI_KEY=your-key
export PRISM_AI_MODEL=your-model
```

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
      serial_fraction: 0.15   # for Amdahl's Law

  chain:
    - api_gateway -> database

  resources:
    connection_pool: 20
    network_rtt_internal: 0.5ms

target:
  latency_p99: 50ms
  throughput: 5000 qps
```

Don't want to write YAML from scratch? Use `scan` or `infer` to generate it automatically.

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
      - uses: AnatomyWorkshop/prism-perf@v0.2.0
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

## Keeping topology fresh

Use the included workflow to auto-refresh topology from OTel traces daily:

```yaml
# .github/workflows/topology-refresh.yml
# Runs daily, infers topology from latest trace, opens PR if changed
```

See [`.github/workflows/topology-refresh.yml`](.github/workflows/topology-refresh.yml) for the full workflow.

## Run tests

```bash
python -m pytest tests/
```

## License

MIT
