"""Constraint library: mathematical bounds for performance impossibility detection."""
from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import Enum
from typing import List, Optional
import math

from topology import Topology, Chain, Service


class Verdict(Enum):
    FEASIBLE = "FEASIBLE"
    INFEASIBLE = "INFEASIBLE"
    UNKNOWN = "UNKNOWN"


@dataclass
class ConstraintResult:
    verdict: Verdict
    constraint_name: str
    evidence: str
    bound_type: str  # "worst-case" | "exact" | "empirical"
    margin: Optional[float] = None  # how close to boundary (positive = feasible headroom)
    details: Optional[dict] = None


class Constraint(ABC):
    @property
    @abstractmethod
    def name(self) -> str:
        ...

    @abstractmethod
    def check(self, topology: Topology) -> ConstraintResult:
        ...


class SerialLatencySum(Constraint):
    """
    p99 of a serial chain >= sum of individual p99 latencies + inter-service RTT.
    Worst-case correlation bound: always sound (if we say INFEASIBLE, it's true).
    """

    @property
    def name(self) -> str:
        return "Serial Latency Sum"

    def check(self, topology: Topology) -> ConstraintResult:
        if topology.target.latency_p99 is None:
            return ConstraintResult(
                verdict=Verdict.UNKNOWN,
                constraint_name=self.name,
                evidence="No latency_p99 target specified.",
                bound_type="N/A",
            )

        results = []
        for chain in topology.chains:
            total_latency = 0.0
            breakdown = []

            for step in chain.steps:
                if step.is_fanout:
                    # Fan-out: p99 = max of parallel branches (extreme value bound)
                    branch_lats = []
                    for svc_name in step.services:
                        svc = topology.services.get(svc_name)
                        if svc:
                            lat = svc.latency_p99 or svc.latency_per_op or 0.0
                            branch_lats.append((svc_name, lat))
                    if branch_lats:
                        max_lat = max(lat for _, lat in branch_lats)
                        branch_str = ", ".join(f"{n}={l:.1f}ms" for n, l in branch_lats)
                        breakdown.append(f"max({branch_str})={max_lat:.1f}ms")
                        total_latency += max_lat
                else:
                    svc = topology.services.get(step.services[0])
                    if svc:
                        lat = svc.latency_p99 or svc.latency_per_op or 0.0
                        total_latency += lat
                        breakdown.append(f"{svc.name}={lat:.1f}ms")

            n_hops = len(chain.steps) - 1
            rtt = topology.resources.network_rtt_internal or 0.0
            rtt_total = n_hops * rtt
            total_latency += rtt_total
            if rtt_total > 0:
                breakdown.append(f"RTT={n_hops}×{rtt:.1f}ms={rtt_total:.1f}ms")

            target = topology.target.latency_p99
            margin = target - total_latency

            chain_str = " → ".join(step.name for step in chain.steps)
            evidence_lines = [
                f"Chain: {chain_str}",
                f"Components: {' + '.join(breakdown)}",
                f"Lower bound: {total_latency:.1f}ms",
                f"Target: {target:.1f}ms",
            ]

            if margin < 0:
                evidence_lines.append(f"Gap: {-margin:.1f}ms ({total_latency/target:.1f}× over target)")
                results.append(ConstraintResult(
                    verdict=Verdict.INFEASIBLE,
                    constraint_name=self.name,
                    evidence="\n".join(evidence_lines),
                    bound_type="worst-case",
                    margin=margin,
                    details={"chain": chain_str, "bound_ms": total_latency, "target_ms": target},
                ))
            else:
                evidence_lines.append(f"Headroom: {margin:.1f}ms")
                results.append(ConstraintResult(
                    verdict=Verdict.FEASIBLE,
                    constraint_name=self.name,
                    evidence="\n".join(evidence_lines),
                    bound_type="worst-case",
                    margin=margin,
                    details={"chain": chain_str, "bound_ms": total_latency, "target_ms": target},
                ))

        infeasible = [r for r in results if r.verdict == Verdict.INFEASIBLE]
        if infeasible:
            worst = min(infeasible, key=lambda r: r.margin)
            return worst

        if results:
            tightest = min(results, key=lambda r: r.margin)
            return tightest

        return ConstraintResult(
            verdict=Verdict.UNKNOWN,
            constraint_name=self.name,
            evidence="No chains defined.",
            bound_type="N/A",
        )


class LittlesLaw(Constraint):
    """
    Little's Law: L = λW.
    Required concurrency = throughput × average latency.
    If required > available (connection pool), INFEASIBLE.
    """

    @property
    def name(self) -> str:
        return "Little's Law"

    def check(self, topology: Topology) -> ConstraintResult:
        if topology.target.throughput_qps is None:
            return ConstraintResult(
                verdict=Verdict.UNKNOWN,
                constraint_name=self.name,
                evidence="No throughput target specified.",
                bound_type="N/A",
            )

        violations = []
        feasible_results = []

        for svc in topology.services.values():
            pool = svc.connections or topology.resources.connection_pool
            if pool is None:
                continue

            avg_latency_s = None
            if svc.latency_per_op is not None:
                avg_latency_s = svc.latency_per_op / 1000.0
            elif svc.latency_p50 is not None:
                avg_latency_s = svc.latency_p50 / 1000.0
            else:
                continue

            required_concurrency = topology.target.throughput_qps * avg_latency_s
            max_throughput = pool / avg_latency_s

            margin = max_throughput - topology.target.throughput_qps

            evidence_lines = [
                f"Service: {svc.name}",
                f"Required concurrency: {topology.target.throughput_qps:.0f} QPS × {avg_latency_s*1000:.1f}ms = {required_concurrency:.0f}",
                f"Available: {pool} connections",
                f"Max achievable: {max_throughput:.0f} QPS",
            ]

            if required_concurrency > pool:
                shortfall = topology.target.throughput_qps - max_throughput
                evidence_lines.append(f"Shortfall: {shortfall:.0f} QPS")
                violations.append(ConstraintResult(
                    verdict=Verdict.INFEASIBLE,
                    constraint_name=self.name,
                    evidence="\n".join(evidence_lines),
                    bound_type="exact",
                    margin=-shortfall,
                    details={"service": svc.name, "required": required_concurrency, "available": pool},
                ))
            else:
                evidence_lines.append(f"Headroom: {margin:.0f} QPS")
                feasible_results.append(ConstraintResult(
                    verdict=Verdict.FEASIBLE,
                    constraint_name=self.name,
                    evidence="\n".join(evidence_lines),
                    bound_type="exact",
                    margin=margin,
                ))

        if violations:
            return min(violations, key=lambda r: r.margin)
        if feasible_results:
            return min(feasible_results, key=lambda r: r.margin)

        return ConstraintResult(
            verdict=Verdict.UNKNOWN,
            constraint_name=self.name,
            evidence="No services with connection pool + latency data.",
            bound_type="N/A",
        )


class AmdahlsLaw(Constraint):
    """
    Amdahl's Law for single-service horizontal scaling.
    Applies when a service declares parallelism and serial_fraction.
    Speedup ≤ 1/(s + (1-s)/N). If target requires speedup beyond this, INFEASIBLE.
    """

    @property
    def name(self) -> str:
        return "Amdahl's Law"

    def check(self, topology: Topology) -> ConstraintResult:
        results = []

        for svc in topology.services.values():
            if svc.parallelism is None or svc.serial_fraction is None:
                continue

            n = svc.parallelism
            s = svc.serial_fraction
            max_speedup = 1.0 / (s + (1.0 - s) / n)
            theoretical_max = 1.0 / s if s > 0 else float("inf")

            base_latency = svc.latency_p99 or svc.latency_per_op
            if base_latency is None:
                continue

            actual_latency = base_latency / max_speedup
            wasted_parallelism = n - (1.0 / s) if s > 0 and n > 1.0 / s else 0

            evidence_lines = [
                f"Service: {svc.name}",
                f"Parallelism: {n} replicas, serial fraction: {s:.0%}",
                f"Max speedup (Amdahl): {max_speedup:.2f}× (theoretical limit: {theoretical_max:.1f}×)",
                f"Effective latency: {base_latency:.1f}ms / {max_speedup:.2f} = {actual_latency:.1f}ms",
            ]

            if wasted_parallelism > 0:
                evidence_lines.append(
                    f"Wasted replicas: {wasted_parallelism:.0f} nodes beyond saturation point"
                )
                results.append(ConstraintResult(
                    verdict=Verdict.INFEASIBLE,
                    constraint_name=self.name,
                    evidence="\n".join(evidence_lines),
                    bound_type="exact",
                    margin=-wasted_parallelism,
                    details={
                        "service": svc.name,
                        "max_speedup": max_speedup,
                        "wasted": wasted_parallelism,
                    },
                ))
            else:
                evidence_lines.append(f"Scaling efficient: all {n} replicas contribute.")
                results.append(ConstraintResult(
                    verdict=Verdict.FEASIBLE,
                    constraint_name=self.name,
                    evidence="\n".join(evidence_lines),
                    bound_type="exact",
                    margin=theoretical_max - max_speedup,
                ))

        if not results:
            return ConstraintResult(
                verdict=Verdict.UNKNOWN,
                constraint_name=self.name,
                evidence="No services declare parallelism + serial_fraction.",
                bound_type="N/A",
            )

        infeasible = [r for r in results if r.verdict == Verdict.INFEASIBLE]
        if infeasible:
            return min(infeasible, key=lambda r: r.margin)
        return min(results, key=lambda r: r.margin)


class MMcQueueStability(Constraint):
    """
    M/M/c queue stability condition: λ < c×μ.
    If arrival rate ≥ service capacity, queue grows unbounded.
    Also computes Erlang-C p99 latency lower bound when stable.
    """

    @property
    def name(self) -> str:
        return "M/M/c Queue Stability"

    def _erlang_c(self, c: int, rho: float) -> float:
        """Probability of queuing (Erlang-C formula), computed in log-space to avoid overflow."""
        if rho >= 1.0:
            return 1.0
        a = c * rho
        # Use log-space to avoid factorial overflow for large c
        log_a = math.log(a) if a > 0 else float('-inf')
        log_terms = [k * log_a - sum(math.log(i) for i in range(1, k + 1)) for k in range(c)]
        max_log = max(log_terms) if log_terms else 0
        sum_terms = sum(math.exp(lt - max_log) for lt in log_terms) * math.exp(max_log)
        log_last = c * log_a - sum(math.log(i) for i in range(1, c + 1)) - math.log(1 - rho)
        last_term = math.exp(log_last)
        p0 = 1.0 / (sum_terms + last_term)
        pc = last_term * p0
        return min(pc, 1.0)

    def check(self, topology: Topology) -> ConstraintResult:
        if topology.target.throughput_qps is None:
            return ConstraintResult(
                verdict=Verdict.UNKNOWN,
                constraint_name=self.name,
                evidence="No throughput target specified.",
                bound_type="N/A",
            )

        results = []
        lam = topology.target.throughput_qps

        for svc in topology.services.values():
            service_time_ms = svc.latency_per_op or svc.latency_p50
            if service_time_ms is None:
                continue

            c = svc.connections or svc.parallelism
            if c is None:
                c = topology.resources.connection_pool
            if c is None:
                continue

            mu = 1000.0 / service_time_ms  # service rate per server (ops/sec)
            total_capacity = c * mu
            rho = lam / total_capacity  # utilization per server

            evidence_lines = [
                f"Service: {svc.name}",
                f"Arrival rate λ = {lam:.0f} QPS",
                f"Servers c = {c}, service rate μ = {mu:.0f} ops/s each",
                f"Total capacity c×μ = {total_capacity:.0f} QPS",
                f"Utilization ρ = {rho:.3f}",
            ]

            if rho >= 1.0:
                evidence_lines.append(f"UNSTABLE: λ ≥ c×μ → queue grows unbounded → latency → ∞")
                results.append(ConstraintResult(
                    verdict=Verdict.INFEASIBLE,
                    constraint_name=self.name,
                    evidence="\n".join(evidence_lines),
                    bound_type="exact",
                    margin=-(lam - total_capacity),
                    details={"service": svc.name, "rho": rho, "capacity": total_capacity},
                ))
            else:
                pc = self._erlang_c(c, rho)
                avg_wait = (pc / (c * mu * (1 - rho))) * 1000  # ms
                p99_wait = avg_wait * math.log(100 * pc) if pc > 0.01 else 0
                p99_total = service_time_ms + max(p99_wait, 0)

                evidence_lines.append(f"Stable. Erlang-C P(queue) = {pc:.3f}")
                evidence_lines.append(f"Estimated p99 wait: {max(p99_wait, 0):.1f}ms")
                evidence_lines.append(f"Estimated p99 total: {p99_total:.1f}ms")

                margin = total_capacity - lam
                results.append(ConstraintResult(
                    verdict=Verdict.FEASIBLE,
                    constraint_name=self.name,
                    evidence="\n".join(evidence_lines),
                    bound_type="exact",
                    margin=margin,
                    details={"service": svc.name, "rho": rho, "p99_ms": p99_total},
                ))

        if not results:
            return ConstraintResult(
                verdict=Verdict.UNKNOWN,
                constraint_name=self.name,
                evidence="No services with latency + concurrency data for queue analysis.",
                bound_type="N/A",
            )

        infeasible = [r for r in results if r.verdict == Verdict.INFEASIBLE]
        if infeasible:
            return min(infeasible, key=lambda r: r.margin)
        return min(results, key=lambda r: r.margin)


ALL_CONSTRAINTS: List[Constraint] = [
    SerialLatencySum(),
    LittlesLaw(),
    AmdahlsLaw(),
    MMcQueueStability(),
]
