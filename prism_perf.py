"""prism-perf: Performance impossibility detection for microservice architectures."""
import sys
import os
import json
import hashlib
import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from topology import load_topology
from solver import solve
from evidence import format_verdict
from infer import infer_topology
from scanner import scan_project
from ai_estimator import estimate_latencies, apply_estimates_to_yaml
from advisor import get_optimization_advice, format_advice
from patterns import match_pattern, format_patterns
from forecaster import forecast_peak_qps, format_forecast, generate_sample_traffic_data


def cmd_check(args):
    """Check a topology YAML for performance feasibility."""
    if not args:
        print("Usage: prism-perf check <topology.yaml> [--advise] [--traffic <data.csv>]")
        sys.exit(1)

    path = args[0]
    advise = "--advise" in args
    traffic_path = None
    if "--traffic" in args:
        idx = args.index("--traffic")
        if idx + 1 < len(args):
            traffic_path = args[idx + 1]

    if not os.path.exists(path):
        print(f"Error: file not found: {path}")
        sys.exit(1)

    topology = load_topology(path)

    # Apply traffic forecast to throughput target if provided
    if traffic_path:
        if not os.path.exists(traffic_path):
            print(f"Warning: traffic file not found: {traffic_path}")
        else:
            forecast = forecast_peak_qps(traffic_path)
            print(format_forecast(forecast))
            if forecast and "forecast_p99_qps" in forecast:
                topology.target.throughput_qps = forecast["forecast_p99_qps"]
                print(f"  Using forecast p99 QPS ({forecast['forecast_p99_qps']}) as throughput target.\n")

    verdict, results = solve(topology)
    report = format_verdict(verdict, results, topology.name)
    print(report)

    # Pattern matching (always shown for INFEASIBLE)
    if verdict.value == "INFEASIBLE":
        pattern_matches = match_pattern(topology, results)
        if pattern_matches:
            print(format_patterns(pattern_matches))

    # AI advice (opt-in)
    if advise and verdict.value == "INFEASIBLE":
        print("Consulting AI advisor ...")
        advice = get_optimization_advice(topology, verdict, results)
        print(format_advice(advice))

    _log_result(topology, verdict, results)

    return 0 if verdict.value == "FEASIBLE" else 1


def cmd_scan(args):
    """Scan a project directory and infer topology."""
    project_dir = args[0] if args else "."
    output_path = None
    use_ai = "--ai" in args

    if "--output" in args:
        idx = args.index("--output")
        if idx + 1 < len(args):
            output_path = args[idx + 1]

    if not os.path.isdir(project_dir):
        print(f"Error: not a directory: {project_dir}")
        sys.exit(1)

    print(f"Scanning {os.path.abspath(project_dir)} ...")
    yaml_content = scan_project(project_dir)

    if yaml_content is None:
        print("No topology sources found.")
        print("Supported: docker-compose.yml, k8s/ manifests, openapi.yaml")
        print("Try: prism-perf infer <trace.json>")
        sys.exit(1)

    if use_ai:
        # Extract service names from YAML
        service_names = [
            line.split("name:")[-1].strip()
            for line in yaml_content.split("\n")
            if "    - name:" in line
        ]
        if service_names:
            print(f"Estimating latencies for {len(service_names)} services via AI ...")
            estimates = estimate_latencies(project_dir, service_names)
            if estimates:
                yaml_content = apply_estimates_to_yaml(yaml_content, estimates)
                print(f"  Estimated: {', '.join(estimates.keys())}")
            else:
                print("  No ANTHROPIC_API_KEY found — skipping AI estimation")
                print("  Set ANTHROPIC_API_KEY or add to .env to enable")

    if output_path:
        os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
        with open(output_path, "w", encoding="utf-8") as f:
            f.write(yaml_content)
        print(f"Topology written to: {output_path}")
        print("Next: verify chain and target, then run:")
        print(f"  python prism_perf.py check {output_path}")
    else:
        print(yaml_content)


def _log_result(topology, verdict, results):
    """Append anonymized result to local dataset for analysis."""
    dataset_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "dataset.jsonl")

    service_names = []
    if hasattr(topology, 'services'):
        for s in topology.services:
            if hasattr(s, 'name'):
                service_names.append(s.name)
            elif isinstance(s, str):
                service_names.append(s)

    topo_hash = hashlib.sha256(str(sorted(service_names)).encode()).hexdigest()[:12]

    binding = None
    for r in results:
        if hasattr(r, 'verdict') and hasattr(r.verdict, 'value') and r.verdict.value == "INFEASIBLE":
            binding = getattr(r, 'name', str(type(r).__name__))
            break

    record = {
        "ts": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "topology_hash": topo_hash,
        "service_count": len(service_names),
        "verdict": verdict.value,
        "binding_constraint": binding,
    }

    try:
        with open(dataset_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(record) + "\n")
    except Exception:
        pass


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


def cmd_demo(args):
    """Run a built-in demo showing INFEASIBLE and FEASIBLE verdicts."""
    import shutil

    demo_infeasible = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                   "examples", "demo_infeasible.yaml")
    demo_fanout = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                               "examples", "demo_fanout.yaml")

    print("=" * 60)
    print("PRISM-PERF DEMO")
    print("=" * 60)
    print()
    print("Demo 1: Serial checkout chain (should be INFEASIBLE)")
    print(f"  topology: examples/demo_infeasible.yaml")
    print()

    topology = load_topology(demo_infeasible)
    verdict, results = solve(topology)
    print(format_verdict(verdict, results, topology.name))

    pattern_matches = match_pattern(topology, results)
    if pattern_matches:
        print(format_patterns(pattern_matches))

    print()
    print("-" * 60)
    print()
    print("Demo 2: Fan-out product page API (should be FEASIBLE)")
    print(f"  topology: examples/demo_fanout.yaml")
    print()

    topology2 = load_topology(demo_fanout)
    verdict2, results2 = solve(topology2)
    print(format_verdict(verdict2, results2, topology2.name))

    print()
    print("=" * 60)
    print("Try it yourself:")
    print("  python prism_perf.py check examples/demo_infeasible.yaml")
    print("  python prism_perf.py check examples/demo_fanout.yaml")
    print("  python prism_perf.py check examples/payment_chain.yaml --advise")
    print("=" * 60)


def main():
    if len(sys.argv) < 2:
        print("prism-perf: Performance impossibility detection")
        print("")
        print("Commands:")
        print("  demo                     Run built-in demo (INFEASIBLE + FEASIBLE examples)")
        print("  check <topology.yaml>    Check if SLA target is feasible")
        print("                           --advise          get AI optimization advice")
        print("                           --traffic <csv>   use traffic forecast as throughput target")
        print("  scan [dir]               Auto-detect topology from project files")
        print("                           --output <path>   write to file")
        print("                           --ai              estimate latencies from source code")
        print("  forecast <traffic.csv>   Forecast peak QPS from historical data")
        print("                           --days N          forecast horizon (default: 30)")
        print("  infer <trace.json>       Generate topology YAML from OTel trace")
        print("                           --output <path>     write to file")
        print("                           --preserve-target   keep existing target/resources")
        print("  sample-traffic [path]    Generate sample traffic CSV for testing")
        print("")
        print("Quick start:")
        print("  python prism_perf.py demo")
        print("  python prism_perf.py check examples/payment_chain.yaml --advise")
        sys.exit(0)

    cmd = sys.argv[1]
    args = sys.argv[2:]

    if cmd == "demo":
        cmd_demo(args)
    elif cmd == "check":
        sys.exit(cmd_check(args))
    elif cmd == "infer":
        cmd_infer(args)
    elif cmd == "scan":
        cmd_scan(args)
    elif cmd == "forecast":
        if not args:
            print("Usage: prism-perf forecast <traffic.csv> [--days 30]")
            sys.exit(1)
        days = 30
        if "--days" in args:
            idx = args.index("--days")
            if idx + 1 < len(args):
                days = int(args[idx + 1])
        forecast = forecast_peak_qps(args[0], horizon_days=days)
        print(format_forecast(forecast))
    elif cmd == "sample-traffic":
        out = args[0] if args else "sample_traffic.csv"
        generate_sample_traffic_data(out)
        print(f"Sample traffic data written to: {out}")
        print(f"Try: python prism_perf.py forecast {out}")
    else:
        print(f"Unknown command: {cmd}")
        print("Run without arguments for help.")
        sys.exit(1)


if __name__ == "__main__":
    main()
