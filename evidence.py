"""Evidence formatter: human-readable proof output."""
from typing import List
from constraints import ConstraintResult, Verdict


def format_verdict(overall: Verdict, results: List[ConstraintResult], topology_name: str) -> str:
    """Format full verdict report."""
    lines = []
    lines.append(f"{'='*60}")
    lines.append(f"PRISM-PERF: {topology_name}")
    lines.append(f"{'='*60}")
    lines.append("")
    lines.append(f"VERDICT: {overall.value}")

    if overall == Verdict.INFEASIBLE:
        lines.append("")
        lines.append("NOTE: This verdict assumes the topology YAML accurately describes")
        lines.append("your system. Results are invalid if caching layers, async decoupling,")
        lines.append("or circuit breakers are present but not declared.")

    lines.append("")

    infeasible = [r for r in results if r.verdict == Verdict.INFEASIBLE]
    feasible = [r for r in results if r.verdict == Verdict.FEASIBLE]
    unknown = [r for r in results if r.verdict == Verdict.UNKNOWN]

    if infeasible:
        lines.append("BINDING CONSTRAINTS:")
        lines.append("")
        for i, r in enumerate(infeasible, 1):
            lines.append(f"  {i}. {r.constraint_name} [{r.bound_type} bound]")
            for eline in r.evidence.split("\n"):
                lines.append(f"     {eline}")
            lines.append("")

    if feasible:
        lines.append("PASSING CONSTRAINTS:")
        lines.append("")
        for r in feasible:
            margin_str = f" (margin: {r.margin:.1f})" if r.margin is not None else ""
            lines.append(f"  - {r.constraint_name}: OK{margin_str}")
        lines.append("")

    if unknown:
        lines.append("INSUFFICIENT DATA:")
        lines.append("")
        for r in unknown:
            lines.append(f"  - {r.constraint_name}: {r.evidence}")
        lines.append("")

    if infeasible:
        lines.append("RECOMMENDATIONS:")
        lines.append("")
        for r in infeasible:
            lines.extend(_recommend(r))
        lines.append("")

    return "\n".join(lines)


def _recommend(result: ConstraintResult) -> List[str]:
    """Generate recommendations for an infeasible constraint."""
    recs = []
    details = result.details or {}

    if result.constraint_name == "Serial Latency Sum":
        recs.append("  - Identify the highest-latency service in the chain and make it async")
        recs.append("  - Or: split the chain into parallel branches where possible")
        recs.append("  - Or: reduce individual service latencies (caching, connection reuse)")

    elif result.constraint_name == "Little's Law":
        svc = details.get("service", "bottleneck service")
        required = details.get("required", "?")
        available = details.get("available", "?")
        recs.append(f"  - Increase connection pool for {svc}: need ≥ {required:.0f}, have {available}")
        recs.append(f"  - Or: reduce per-operation latency (faster queries, read replicas)")
        recs.append(f"  - Or: reduce throughput target")

    elif result.constraint_name == "Amdahl's Law":
        svc = details.get("service", "service")
        max_sp = details.get("max_speedup", "?")
        recs.append(f"  - {svc}: adding more replicas won't help (max speedup = {max_sp:.1f}×)")
        recs.append(f"  - Reduce serial fraction (optimize initialization, locking, commit)")
        recs.append(f"  - Or: redesign to eliminate serial dependency")

    elif result.constraint_name == "M/M/c Queue Stability":
        svc = details.get("service", "service")
        recs.append(f"  - {svc}: system is unstable at target load")
        recs.append(f"  - Add more servers/connections to increase total capacity above arrival rate")
        recs.append(f"  - Or: reduce per-request service time")

    else:
        recs.append(f"  - Address {result.constraint_name} violation")

    return recs
