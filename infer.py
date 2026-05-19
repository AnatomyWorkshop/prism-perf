"""Infer topology from OpenTelemetry JSON trace export."""
import json
import statistics
from collections import defaultdict
from typing import Dict, List, Any


def infer_topology(trace_path: str) -> str:
    """
    Read OTel JSON trace file, extract:
    - Service names from span attributes
    - p50/p99 latencies per service
    - Call chain structure from parent-child relationships

    Output: skeleton YAML that user fills with target + resources.
    """
    with open(trace_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    spans = _extract_spans(data)
    if not spans:
        return "# No spans found in trace file.\n"

    services = _compute_service_stats(spans)
    chains = _infer_chains(spans)

    return _generate_yaml(services, chains)


def _extract_spans(data: Any) -> List[Dict]:
    """Extract spans from various OTel JSON formats."""
    spans = []

    if isinstance(data, list):
        for item in data:
            if "resourceSpans" in item:
                spans.extend(_extract_from_otlp(item))
            elif "traceID" in item or "traceId" in item:
                spans.append(_normalize_span(item))
    elif isinstance(data, dict):
        if "resourceSpans" in data:
            spans.extend(_extract_from_otlp(data))
        elif "data" in data:
            for item in data["data"]:
                if "spans" in item:
                    for s in item["spans"]:
                        spans.append(_normalize_span(s))

    return spans


def _extract_from_otlp(data: Dict) -> List[Dict]:
    """Extract from OTLP JSON format."""
    spans = []
    for rs in data.get("resourceSpans", []):
        service_name = "unknown"
        for attr in rs.get("resource", {}).get("attributes", []):
            if attr.get("key") == "service.name":
                service_name = attr.get("value", {}).get("stringValue", "unknown")

        for ss in rs.get("scopeSpans", []):
            for span in ss.get("spans", []):
                spans.append({
                    "service": service_name,
                    "name": span.get("name", ""),
                    "span_id": span.get("spanId", ""),
                    "parent_span_id": span.get("parentSpanId", ""),
                    "start_ns": int(span.get("startTimeUnixNano", 0)),
                    "end_ns": int(span.get("endTimeUnixNano", 0)),
                })
    return spans


def _normalize_span(span: Dict) -> Dict:
    """Normalize a Jaeger/Zipkin-style span."""
    service = span.get("process", {}).get("serviceName", "")
    if not service:
        service = span.get("localEndpoint", {}).get("serviceName", "unknown")

    start = span.get("startTime", 0)
    duration = span.get("duration", 0)

    return {
        "service": service,
        "name": span.get("operationName", span.get("name", "")),
        "span_id": span.get("spanID", span.get("spanId", span.get("id", ""))),
        "parent_span_id": span.get("parentSpanID", span.get("parentId", "")),
        "start_ns": start * 1000 if start < 1e15 else start,
        "end_ns": (start + duration) * 1000 if start < 1e15 else start + duration,
    }


def _compute_service_stats(spans: List[Dict]) -> Dict[str, Dict]:
    """Compute p50/p99 latency per service."""
    durations_by_service = defaultdict(list)

    for span in spans:
        duration_ms = (span["end_ns"] - span["start_ns"]) / 1_000_000
        if duration_ms > 0:
            durations_by_service[span["service"]].append(duration_ms)

    services = {}
    for svc, durations in durations_by_service.items():
        if not durations:
            continue
        durations.sort()
        n = len(durations)
        p50 = durations[n // 2] if n > 0 else 0
        p99_idx = min(int(n * 0.99), n - 1)
        p99 = durations[p99_idx]
        services[svc] = {"p50": round(p50, 1), "p99": round(p99, 1)}

    return services


def _infer_chains(spans: List[Dict]) -> List[List[str]]:
    """Infer call chains from parent-child span relationships."""
    span_map = {s["span_id"]: s for s in spans if s["span_id"]}
    children = defaultdict(list)
    roots = []

    for span in spans:
        pid = span["parent_span_id"]
        if pid and pid in span_map:
            children[pid].append(span["span_id"])
        elif not pid:
            roots.append(span["span_id"])

    chains = []
    for root_id in roots:
        chain = _walk_chain(root_id, span_map, children)
        if len(chain) > 1:
            chains.append(chain)

    # Deduplicate by service sequence
    seen = set()
    unique_chains = []
    for chain in chains:
        key = tuple(chain)
        if key not in seen:
            seen.add(key)
            unique_chains.append(chain)

    return unique_chains[:5]  # limit to top 5 chains


def _walk_chain(span_id: str, span_map: Dict, children: Dict) -> List[str]:
    """Walk a span tree depth-first, collecting service names in order."""
    chain = []
    visited = set()

    def dfs(sid):
        if sid in visited:
            return
        visited.add(sid)
        span = span_map.get(sid)
        if span:
            if not chain or chain[-1] != span["service"]:
                chain.append(span["service"])
            for child_id in sorted(children.get(sid, []),
                                   key=lambda x: span_map.get(x, {}).get("start_ns", 0)):
                dfs(child_id)

    dfs(span_id)
    return chain


def _generate_yaml(services: Dict[str, Dict], chains: List[List[str]]) -> str:
    """Generate skeleton topology YAML."""
    lines = [
        "# Auto-generated from OTel trace. Fill in target and resources.",
        "topology:",
        f'  name: "inferred-topology"',
        "  services:",
    ]

    for svc_name, stats in sorted(services.items()):
        lines.append(f"    - name: {svc_name}")
        lines.append(f"      type: sync")
        lines.append(f"      latency_p50: {stats['p50']}ms")
        lines.append(f"      latency_p99: {stats['p99']}ms")

    if chains:
        lines.append("")
        lines.append("  chain:")
        for chain in chains:
            lines.append(f"    - {' -> '.join(chain)}")

    lines.append("")
    lines.append("  resources:")
    lines.append("    # TODO: fill in actual resource constraints")
    lines.append("    cpu_cores: 8")
    lines.append("    connection_pool: 50")
    lines.append("    network_rtt_internal: 0.5ms")
    lines.append("")
    lines.append("target:")
    lines.append("  # TODO: fill in your SLA target")
    lines.append("  latency_p99: 100ms")
    lines.append("  throughput: 1000 qps")
    lines.append("")

    return "\n".join(lines)
