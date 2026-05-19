"""Tests for scanner and real-world topologies."""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scanner import scan_project
from topology import load_topology
from solver import solve
from constraints import Verdict


def test_scan_compose():
    """Scan sample docker-compose project."""
    project_dir = os.path.join(os.path.dirname(__file__), "..", "examples", "sample-compose")
    result = scan_project(project_dir)
    assert result is not None, "scan_project returned None"
    assert "topology:" in result
    assert "chain:" in result
    assert "WARNING" in result, "Should warn about depends_on inference"
    print(f"  PASS: scan docker-compose → found topology with chain")


def test_scan_nonexistent():
    """Scan directory with no topology sources."""
    result = scan_project(os.path.dirname(__file__))
    assert result is None, "Should return None for directory without topology sources"
    print(f"  PASS: scan empty dir → None")


def test_robot_shop_infeasible():
    """Robot Shop with realistic latencies: 200ms target is impossible."""
    topo = load_topology(os.path.join(os.path.dirname(__file__), "..", "examples", "robot-shop-realistic.yaml"))
    verdict, results = solve(topo)
    assert verdict == Verdict.INFEASIBLE
    infeasible_names = [r.constraint_name for r in results if r.verdict == Verdict.INFEASIBLE]
    assert "Serial Latency Sum" in infeasible_names
    print(f"  PASS: Robot Shop → INFEASIBLE (bound >> 200ms target)")


def test_ewolff_infeasible():
    """ewolff Spring Cloud: 100ms target with 4-service chain is impossible."""
    topo = load_topology(os.path.join(os.path.dirname(__file__), "..", "examples", "ewolff-realistic.yaml"))
    verdict, results = solve(topo)
    assert verdict == Verdict.INFEASIBLE
    infeasible_names = [r.constraint_name for r in results if r.verdict == Verdict.INFEASIBLE]
    assert "Serial Latency Sum" in infeasible_names
    print(f"  PASS: ewolff → INFEASIBLE (271.5ms > 100ms target)")


def test_robot_shop_relaxed_feasible():
    """Robot Shop with relaxed target (2000ms) should be feasible if pool is large enough."""
    topo = load_topology(os.path.join(os.path.dirname(__file__), "..", "examples", "robot-shop-realistic.yaml"))
    topo.target.latency_p99 = 2000.0
    topo.target.throughput_qps = 10.0
    topo.resources.connection_pool = 100
    verdict, results = solve(topo)
    assert verdict == Verdict.FEASIBLE, f"Expected FEASIBLE with relaxed target, got {verdict}"
    print(f"  PASS: Robot Shop (relaxed 2000ms, 10 QPS) → FEASIBLE")


def test_false_positive_awareness():
    """Verify that INFEASIBLE output includes assumption disclaimer."""
    from evidence import format_verdict
    topo = load_topology(os.path.join(os.path.dirname(__file__), "..", "examples", "payment_chain.yaml"))
    verdict, results = solve(topo)
    report = format_verdict(verdict, results, topo.name)
    assert "assumes the topology YAML accurately describes" in report
    assert "caching layers" in report
    print(f"  PASS: INFEASIBLE report includes assumption disclaimer")


def run_all():
    print("=" * 60)
    print("PRISM-PERF INTEGRATION TESTS")
    print("=" * 60)
    print()

    tests = [
        test_scan_compose,
        test_scan_nonexistent,
        test_robot_shop_infeasible,
        test_ewolff_infeasible,
        test_robot_shop_relaxed_feasible,
        test_false_positive_awareness,
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
