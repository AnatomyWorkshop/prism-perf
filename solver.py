"""Solver: iterate constraints, collect violations, produce verdict."""
from typing import List, Tuple
from topology import Topology
from constraints import Constraint, ConstraintResult, Verdict, ALL_CONSTRAINTS


def solve(topology: Topology, constraints: List[Constraint] = None) -> Tuple[Verdict, List[ConstraintResult]]:
    """
    Run all constraints against topology.
    Returns (overall_verdict, list_of_results).
    """
    if constraints is None:
        constraints = ALL_CONSTRAINTS

    results = []
    for constraint in constraints:
        result = constraint.check(topology)
        results.append(result)

    infeasible = [r for r in results if r.verdict == Verdict.INFEASIBLE]
    unknowns = [r for r in results if r.verdict == Verdict.UNKNOWN]
    feasible = [r for r in results if r.verdict == Verdict.FEASIBLE]

    if infeasible:
        return Verdict.INFEASIBLE, results
    elif unknowns and not feasible:
        return Verdict.UNKNOWN, results
    else:
        return Verdict.FEASIBLE, results
