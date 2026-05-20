# prism-perf Roadmap

## Shipped (v0.2.0)

- Four mathematical constraints: Serial Latency Sum, Little's Law, Amdahl's Law, M/M/c Queue Stability
- α-index: resource overflow ratio displayed per constraint (α = demand / capacity)
- Architecture pattern library: CQRS, Saga, Bulkhead, Cache-Aside, Async Fan-out
- AI optimization advice (`--advise`) via Deepseek / Anthropic / any OpenAI-compatible endpoint
- Traffic forecasting (`--traffic`, `forecast` command)
- Auto-scan from docker-compose, Kubernetes manifests, OpenAPI specs
- AI latency estimation from source code (`scan --ai`)
- OTel trace → topology YAML (`infer`)
- GitHub Action with PR comment and merge blocking
- Daily topology refresh workflow

---

## Near-term (v0.3 — do when first users appear)

These are small, high-signal improvements that make the tool more useful without changing the architecture.

### Better α-index output
- Show α for all services, not just the binding one
- Color-coded terminal output (red/yellow/green) when TTY is detected
- Machine-readable JSON output mode (`--json`) for CI integration

### More constraints
- **Bandwidth bound**: `payload_size × throughput > network_bandwidth → INFEASIBLE`
  - Catches cases where the math is fine but the wire can't carry the data
- **Fan-out amplification**: when one request triggers N downstream calls, multiply throughput by N
  - Common in BFF / aggregator patterns; currently invisible to the engine
- **Cold start latency**: for serverless / autoscaling services, add startup time to p99 under burst load

### Topology improvements
- Support parallel branches (not just serial chains) in topology YAML
- `depends_on` in docker-compose → infer likely call direction, not just startup order
- Kubernetes resource limits → auto-populate `connections` and `parallelism` fields

---

## Medium-term (v1.0 — do when there is traction)

### What-If simulation mode
Developers define topology at design time, before any code is written. The engine runs
symbolic pressure tests in CI and rejects architectures that are provably infeasible.

```yaml
# prism-perf.yaml — checked at compile time, not runtime
simulation:
  traffic_model: poisson
  peak_qps: 50000
  growth_rate: 2x_per_year
```

This shifts performance validation from post-deployment load testing to pre-development
architectural review. The constraint engine already does the math; this just adds a
design-time entry point.

### Incremental constraint learning
When a user marks a verdict as wrong ("this was actually feasible"), record the
discrepancy in `dataset.jsonl` with the topology hash. Over time, identify which
constraints produce false positives for which topology shapes, and adjust confidence
levels accordingly.

### VS Code / JetBrains extension
Inline α-index annotations on topology YAML files. Red gutter markers on services
where α > 1. Hover to see the constraint proof. No CLI required.

---

## Long-term (post-traction, requires domain expertise)

### ML training pipeline analysis (`prism-perf-ml`)
Apply the same constraint engine to distributed ML training:

| Constraint | What it catches |
|---|---|
| Roofline Model | GPU compute vs. memory bandwidth bound |
| Pipeline parallelism bubble | Idle GPU fraction from pipeline depth |
| NCCL communication bound | All-reduce time > compute time → scaling wall |
| OOM prediction | Activation memory + optimizer state > GPU VRAM |

Most teams discover these limits by running expensive training jobs that crash or
stall. prism-perf-ml would catch them before the first GPU-hour is spent.

This is a separate product with a different user (ML engineers, not SREs). Build it
after the microservice version has demonstrated the model works.

### CI/CD pipeline analysis
Apply queuing theory to build pipelines:
- Critical path of the dependency DAG → minimum possible build time
- Runner pool sizing via Little's Law (concurrent builds × build time > runner count)
- Test parallelism saturation via Amdahl's Law

### Kubernetes capacity simulation
Given HPA config + traffic forecast + node specs, prove whether the cluster can
absorb a traffic spike before cold-start latency causes cascading failures.

---

## What we will not build

- **A load testing tool.** k6, Locust, and Gatling already exist. prism-perf proves
  limits mathematically; it does not measure them empirically.
- **An APM / observability platform.** Datadog, Grafana, and Honeycomb already exist.
  prism-perf tells you what cannot be; it does not tell you what is.
- **A general-purpose performance profiler.** We only detect architectural impossibilities
  from topology declarations. Per-function profiling is out of scope.
- **A SaaS dashboard** (until there are paying users who ask for it).
