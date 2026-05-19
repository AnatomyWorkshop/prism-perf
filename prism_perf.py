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
        print("Usage: prism-perf infer <trace.json> [--output topology.yaml] [--preserve-target]")
        sys.exit(1)

    trace_path = args[0]
    output_path = None
    preserve_target = "--preserve-target" in args

    if "--output" in args:
        idx = args.index("--output")
        if idx + 1 < len(args):
            output_path = args[idx + 1]

    if not os.path.exists(trace_path):
        print(f"Error: file not found: {trace_path}")
        sys.exit(1)

    yaml_content = infer_topology(trace_path)

    if preserve_target and output_path and os.path.exists(output_path):
        yaml_content = _merge_preserve_target(yaml_content, output_path)

    if output_path:
        os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
        with open(output_path, "w", encoding="utf-8") as f:
            f.write(yaml_content)
        print(f"Topology written to: {output_path}")
    else:
        print(yaml_content)


def _merge_preserve_target(new_yaml: str, existing_path: str) -> str:
    """Keep target and resources sections from existing file, update services/chain from new inference."""
    with open(existing_path, "r", encoding="utf-8") as f:
        existing = f.read()

    # Extract target and resources from existing
    target_section = ""
    resources_section = ""
    in_target = False
    in_resources = False

    for line in existing.split("\n"):
        if line.startswith("target:"):
            in_target = True
            in_resources = False
            target_section = line + "\n"
        elif line.startswith("  resources:"):
            in_resources = True
            in_target = False
            resources_section = line + "\n"
        elif in_target:
            if line and not line[0].isspace():
                in_target = False
            else:
                target_section += line + "\n"
        elif in_resources:
            if line and not line[0].isspace() and not line.startswith("    "):
                in_resources = False
            else:
                resources_section += line + "\n"

    # Replace target/resources in new yaml
    new_lines = []
    skip_until_next_section = False
    for line in new_yaml.split("\n"):
        if line.startswith("target:") or line.strip().startswith("# TODO: fill in your SLA"):
            skip_until_next_section = True
            continue
        if line.startswith("  resources:") or line.strip().startswith("# TODO: fill in actual"):
            skip_until_next_section = True
            continue
        if skip_until_next_section:
            if line and not line.startswith(" ") and not line.startswith("#"):
                skip_until_next_section = False
                new_lines.append(line)
            elif not line:
                skip_until_next_section = False
            continue
        new_lines.append(line)

    result = "\n".join(new_lines).rstrip() + "\n"
    if resources_section:
        result += "\n" + resources_section.rstrip() + "\n"
    if target_section:
        result += "\n" + target_section.rstrip() + "\n"

    return result


def main():
    if len(sys.argv) < 2:
        print("prism-perf: Performance impossibility detection")
        print("")
        print("Commands:")
        print("  check <topology.yaml>    Check if SLA target is feasible")
        print("  infer <trace.json>       Generate topology YAML from OTel trace")
        print("                           --output <path>     write to file")
        print("                           --preserve-target   keep existing target/resources")
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
