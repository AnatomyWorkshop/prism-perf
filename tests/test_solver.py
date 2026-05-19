"""Tests for prism-perf constraint solver."""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from topology import load_topology, Topology, Service, Chain, Resources, Target
from constraints import (
    SerialLatencySum, LittlesLaw, AmdahlsLaw, MMcQueueStability,
    Verdict, ALL_CONSTRAINTS,
)
from solver import solve


def test_serial_latency_infeasible():
    """Payment chain: 538.5ms lower bound vs 100ms target → INFEASIBLE."""
    topo = load_topology(os.path.join(os.path.dirname(__file__), "..", "examples", "payment_chain.yaml"))
    constraint = SerialLatencySum()
    result = constraint.check(topo)
    assert result.verdict == Verdict.INFEASIBLE, f"Expected INFEASIBLE, got {result.verdict}"
    assert result.margin < 0
    print(f"  PASS: Serial Latency Sum → INFEASIBLE (bound={result.details['bound_ms']:.1f}ms > target={result.details['target_ms']:.1f}ms)")


def test_littles_law_infeasible():
    """Payment chain: 10000 QPS × 5ms = 50 connections needed, only 20 → INFEASIBLE."""
    topo = load_topology(os.path.join(os.path.dirname(__file__), "..", "examples", "payment_chain.yaml"))
    constraint = LittlesLaw()
    result = constraint.check(topo)
    assert result.verdict == Verdict.INFEASIBLE, f"Expected INFEASIBLE, got {result.verdict}"
    print(f"  PASS: Little's Law → INFEASIBLE (need {result.details['required']:.0f} connections, have {result.details['available']})")


def test_cdn_feasible():
    """CDN edge: simple chain with generous resources → FEASIBLE."""
    topo = load_topology(os.path.join(os.path.dirname(__file__), "..", "examples", "cdn_edge.yaml"))
    verdict, results = solve(topo)
    assert verdict == Verdict.FEASIBLE, f"Expected FEASIBLE, got {verdict}"
    print(f"  PASS: CDN edge → FEASIBLE")


def test_amdahl_scaling_wall():
    """Scaling wall: 64 replicas with 8% serial → max speedup 12.5×, wasted nodes."""
    topo = load_topology(os.path.join(os.path.dirname(__file__), "..", "examples", "scaling_wall.yaml"))
    constraint = AmdahlsLaw()
    result = constraint.check(topo)
    assert result.verdict == Verdict.INFEASIBLE, f"Expected INFEASIBLE, got {result.verdict}"
    assert result.details["wasted"] > 0
    print(f"  PASS: Amdahl's Law → INFEASIBLE (max speedup={result.details['max_speedup']:.1f}×, wasted={result.details['wasted']:.0f} nodes)")


def test_mmc_stability():
    """Scaling wall: 2000 QPS with 64 servers × 10 ops/s = 640 capacity → UNSTABLE."""
    topo = load_topology(os.path.join(os.path.dirname(__file__), "..", "examples", "scaling_wall.yaml"))
    constraint = MMcQueueStability()
    result = constraint.check(topo)
    # 64 servers × (1000/100) = 640 ops/s capacity, target = 2000 QPS → unstable
    assert result.verdict == Verdict.INFEASIBLE, f"Expected INFEASIBLE, got {result.verdict}"
    print(f"  PASS: M/M/c Queue → INFEASIBLE (ρ={result.details['rho']:.2f})")


def test_full_solve_payment():
    """Full solve on payment chain → INFEASIBLE with multiple binding constraints."""
    topo = load_topology(os.path.join(os.path.dirname(__file__), "..", "examples", "payment_chain.yaml"))
    verdict, results = solve(topo)
    assert verdict == Verdict.INFEASIBLE
    infeasible_names = [r.constraint_name for r in results if r.verdict == Verdict.INFEASIBLE]
    assert "Serial Latency Sum" in infeasible_names
    assert "Little's Law" in infeasible_names
    print(f"  PASS: Full solve payment_chain → INFEASIBLE ({', '.join(infeasible_names)})")


def run_all():
    print("=" * 60)
    print("PRISM-PERF TEST SUITE")
    print("=" * 60)
    print()

    tests = [
        test_serial_latency_infeasible,
        test_littles_law_infeasible,
        test_cdn_feasible,
        test_amdahl_scaling_wall,
        test_mmc_stability,
        test_full_solve_payment,
    ]

    passed = 0
    failed = 0
    for test in tests:
        try:
            test()
            passed += 1
        except AssertionError as e:
            print(f"  FAIL: {test.__name__}: {e}")
            failed += 1
        except Exception as e:
            print(f"  ERROR: {test.__name__}: {type(e).__name__}: {e}")
            failed += 1

    print()
    print(f"Results: {passed} passed, {failed} failed, {passed + failed} total")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(run_all())
