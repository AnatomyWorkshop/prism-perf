"""Architecture pattern library.

Canonical patterns with known performance characteristics.
Used to match a topology to a known pattern and suggest the
optimal form of that pattern.
"""

PATTERNS = {
    "sync_chain": {
        "name": "Synchronous Request Chain",
        "description": "Services call each other synchronously in sequence",
        "latency_model": "sum(p99_i) + n_hops * rtt",
        "scaling_limit": "Amdahl: limited by slowest service",
        "canonical_fix": "Async Fan-out or CQRS",
        "when_to_use": "Simple reads, strong consistency required",
        "anti_pattern_signals": [
            "chain depth > 4",
            "any service p99 > 200ms in chain",
            "payment/shipping in synchronous path",
        ],
    },
    "cache_aside": {
        "name": "Cache-Aside",
        "description": "Application checks cache before hitting DB",
        "latency_model": "hit_rate * cache_p99 + (1-hit_rate) * db_p99",
        "scaling_limit": "Cache memory, eviction policy",
        "canonical_fix": "Increase hit rate via key design",
        "when_to_use": "Read-heavy, tolerate eventual consistency",
        "anti_pattern_signals": [
            "DB p99 > 50ms with no cache declared",
            "read:write ratio > 10:1",
        ],
    },
    "async_fanout": {
        "name": "Async Fan-out (Event-Driven)",
        "description": "Producer emits event, consumers process independently",
        "latency_model": "producer_p99 + queue_p99 (consumer is off critical path)",
        "scaling_limit": "Queue throughput, consumer lag",
        "canonical_fix": "Partition by key for ordering guarantees",
        "when_to_use": "Notifications, audit logs, eventual consistency OK",
        "anti_pattern_signals": [
            "consumer result needed in same request",
            "strong consistency required",
        ],
    },
    "bulkhead": {
        "name": "Bulkhead",
        "description": "Isolate connection pools per downstream service",
        "latency_model": "unchanged, but failures isolated",
        "scaling_limit": "Total connection count",
        "canonical_fix": "Separate thread pools / connection pools per service",
        "when_to_use": "Mixed criticality services, failure isolation needed",
        "anti_pattern_signals": [
            "single shared connection pool",
            "one slow service can block all others",
        ],
    },
    "cqrs": {
        "name": "CQRS (Command Query Responsibility Segregation)",
        "description": "Separate read and write models",
        "latency_model": "reads: read_replica_p99, writes: primary_p99",
        "scaling_limit": "Replication lag for reads",
        "canonical_fix": "Read replicas + materialized views",
        "when_to_use": "Read-heavy with complex queries, write throughput matters",
        "anti_pattern_signals": [
            "read and write on same DB with high QPS",
            "complex aggregation queries on write path",
        ],
    },
    "saga": {
        "name": "Saga (Distributed Transaction)",
        "description": "Long-running transaction as sequence of local transactions with compensations",
        "latency_model": "sum(step_p99) but async — not on request critical path",
        "scaling_limit": "Compensation complexity, idempotency",
        "canonical_fix": "Choreography (events) over orchestration (central coordinator)",
        "when_to_use": "Multi-service transactions, payment flows",
        "anti_pattern_signals": [
            "payment in synchronous chain",
            "distributed transaction spanning > 3 services",
        ],
    },
}


def match_pattern(topology, results) -> list[dict]:
    """
    Match topology to canonical patterns based on structure and violations.
    Returns list of matching patterns sorted by relevance.
    """
    matches = []
    infeasible_constraints = [r.constraint_name for r in results
                               if hasattr(r, "verdict") and r.verdict.value == "INFEASIBLE"]

    services = []
    if hasattr(topology, "services"):
        services = topology.services

    chains = []
    if hasattr(topology, "chains"):
        # Chain objects have .services list
        for c in topology.chains:
            if hasattr(c, "services"):
                chains.append(c.services)
            elif isinstance(c, (list, tuple)):
                chains.append(list(c))

    chain_depth = max((len(c) for c in chains), default=0)
    has_payment = any("payment" in (getattr(s, "name", "") or "").lower()
                      for s in services)
    has_shipping = any("shipping" in (getattr(s, "name", "") or "").lower()
                       for s in services)
    high_latency_in_chain = any(
        getattr(s, "latency_p99", 0) > 200
        for s in services
    )

    # Sync chain → Async Fan-out or CQRS
    if "Serial Latency Sum" in infeasible_constraints:
        if has_payment or has_shipping:
            matches.append({
                "pattern": PATTERNS["saga"],
                "reason": "Payment/shipping in synchronous path — Saga pattern removes them from critical path",
                "priority": 1,
            })
        if high_latency_in_chain:
            matches.append({
                "pattern": PATTERNS["async_fanout"],
                "reason": "High-latency services in chain — async fan-out removes them from p99 calculation",
                "priority": 2,
            })
        if chain_depth > 3:
            matches.append({
                "pattern": PATTERNS["cqrs"],
                "reason": f"Chain depth {chain_depth} — CQRS separates read/write paths to reduce chain length",
                "priority": 3,
            })

    # Little's Law → Bulkhead or Cache-Aside
    if "Little's Law" in infeasible_constraints:
        matches.append({
            "pattern": PATTERNS["bulkhead"],
            "reason": "Connection pool exhaustion — Bulkhead isolates pools to prevent cascade failure",
            "priority": 2,
        })
        matches.append({
            "pattern": PATTERNS["cache_aside"],
            "reason": "High concurrency demand — Cache-Aside reduces DB load and required connections",
            "priority": 3,
        })

    # Deduplicate by pattern name
    seen = set()
    unique = []
    for m in sorted(matches, key=lambda x: x["priority"]):
        name = m["pattern"]["name"]
        if name not in seen:
            seen.add(name)
            unique.append(m)

    return unique[:3]


def format_patterns(matches: list[dict]) -> str:
    """Format pattern matches for terminal output."""
    if not matches:
        return ""

    lines = ["", "CANONICAL ARCHITECTURE PATTERNS:", ""]
    for i, m in enumerate(matches, 1):
        p = m["pattern"]
        lines.append(f"  {i}. {p['name']}")
        lines.append(f"     Why: {m['reason']}")
        lines.append(f"     Model: {p['latency_model']}")
        lines.append(f"     Use when: {p['when_to_use']}")
        lines.append("")

    return "\n".join(lines)
