"""Evidence formatter: human-readable proof output."""
from typing import List
from constraints import ConstraintResult, Verdict


def _compute_alpha(result: ConstraintResult) -> float | None:
    """Compute α = demand / capacity for a constraint result."""
    details = result.details or {}

    if result.constraint_name == "Serial Latency Sum":
        bound = details.get("bound_ms")
        target = details.get("target_ms")
        if bound and target and target > 0:
            return bound / target

    elif result.constraint_name == "Little's Law":
        required = details.get("required")
        available = details.get("available")
        if required and available and available > 0:
            return required / available

    elif result.constraint_name == "Amdahl's Law":
        max_speedup = details.get("max_speedup")
        wasted = details.get("wasted")
        if max_speedup and wasted is not None:
            service = details.get("service", "")
            # α = parallelism / saturation_point
            # wasted > 0 means over-provisioned past the Amdahl limit
            if max_speedup > 0:
                return (max_speedup + wasted) / max_speedup

    elif result.constraint_name == "M/M/c Queue Stability":
        rho = details.get("rho")
        if rho is not None:
            return rho  # ρ ≥ 1 means unstable

    return None


def _alpha_label(alpha: float) -> str:
    """Human-readable severity label for α value."""
    if alpha < 0.5:
        return "Safe"
    elif alpha < 0.8:
        return "Moderate"
    elif alpha < 1.0:
        return "Critical Threshold"
    else:
        return "OVERFLOW"


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

    # α-index summary (resource overflow ratios)
    all_with_alpha = [(r, _compute_alpha(r)) for r in results if _compute_alpha(r) is not None]
    if all_with_alpha:
        lines.append("RESOURCE OVERFLOW INDEX (α = demand / capacity):")
        lines.append("")
        for r, alpha in sorted(all_with_alpha, key=lambda x: -x[1]):
            svc = (r.details or {}).get("service", "system")
            dim = _alpha_dimension(r)
            marker = " ← BINDING" if alpha >= 1.0 else ""
            lines.append(f"  α_{dim:<24} = {alpha:.2f}  ({_alpha_label(alpha)}){marker}")
        lines.append("")

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


def _alpha_dimension(result: ConstraintResult) -> str:
    """Map constraint to a resource dimension name."""
    details = result.details or {}
    svc = details.get("service", "")

    if result.constraint_name == "Serial Latency Sum":
        return "serial_latency"
    elif result.constraint_name == "Little's Law":
        return f"connection_pool({svc})"
    elif result.constraint_name == "Amdahl's Law":
        return f"parallelism({svc})"
    elif result.constraint_name == "M/M/c Queue Stability":
        return f"queue_load({svc})"
    return result.constraint_name


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
