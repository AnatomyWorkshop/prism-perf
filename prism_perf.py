"""prism-perf: Performance impossibility detection for microservice architectures."""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from topology import load_topology
from solver import solve
from evidence import format_verdict
from infer import infer_topology


def cmd_check(args):
    """Check a topology YAML for performance feasibility."""
    if not args:
        print("Usage: prism-perf check <topology.yaml>")
        sys.exit(1)

    path = args[0]
    if not os.path.exists(path):
        print(f"Error: file not found: {path}")
        sys.exit(1)

    topology = load_topology(path)
    verdict, results = solve(topology)
    report = format_verdict(verdict, results, topology.name)
    print(report)

    return 0 if verdict.value == "FEASIBLE" else 1


def cmd_infer(args):
    """Infer topology from OTel JSON trace."""
    if not args:
        print("Usage: prism-perf infer <trace.json> [--output topology.yaml]")
        sys.exit(1)

    trace_path = args[0]
    output_path = None
    if "--output" in args:
        idx = args.index("--output")
        if idx + 1 < len(args):
            output_path = args[idx + 1]

    if not os.path.exists(trace_path):
        print(f"Error: file not found: {trace_path}")
        sys.exit(1)

    yaml_content = infer_topology(trace_path)

    if output_path:
        with open(output_path, "w", encoding="utf-8") as f:
            f.write(yaml_content)
        print(f"Topology written to: {output_path}")
    else:
        print(yaml_content)


def main():
    if len(sys.argv) < 2:
        print("prism-perf: Performance impossibility detection")
        print("")
        print("Commands:")
        print("  check <topology.yaml>    Check if SLA target is feasible")
        print("  infer <trace.json>       Generate topology YAML from OTel trace")
        print("")
        print("Examples:")
        print("  python prism_perf.py check examples/payment_chain.yaml")
        print("  python prism_perf.py infer examples/sample_trace.json --output my_topology.yaml")
        sys.exit(0)

    cmd = sys.argv[1]
    args = sys.argv[2:]

    if cmd == "check":
        sys.exit(cmd_check(args))
    elif cmd == "infer":
        cmd_infer(args)
    else:
        print(f"Unknown command: {cmd}")
        print("Available: check, infer")
        sys.exit(1)


if __name__ == "__main__":
    main()
