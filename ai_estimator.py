"""AI-assisted latency estimation from source code analysis.

Uses Claude API to read service source code and estimate p50/p99 latencies
based on detected patterns: DB queries, HTTP calls, cache operations, etc.

This fills in the TODO latencies that scan produces, making the workflow
truly zero-config for projects with readable source code.
"""
import os
import json
from typing import Optional


def estimate_latencies(project_dir: str, service_names: list[str]) -> dict[str, dict]:
    """
    Analyze source code for each service and estimate latencies.

    Returns: {service_name: {"p50": float_ms, "p99": float_ms, "confidence": str, "reason": str}}
    """
    try:
        import anthropic
    except ImportError:
        return {}

    api_key = (os.environ.get("ANTHROPIC_API_KEY") or
               _read_key_from_env_file(project_dir))
    if not api_key:
        return {}

    client = anthropic.Anthropic(api_key=api_key)
    results = {}

    for service_name in service_names:
        code_snippets = _extract_code_snippets(project_dir, service_name)
        if not code_snippets:
            continue

        estimate = _ask_claude(client, service_name, code_snippets)
        if estimate:
            results[service_name] = estimate

    return results


def _read_key_from_env_file(project_dir: str) -> Optional[str]:
    for fname in [".env", ".env.keys", ".env.local"]:
        path = os.path.join(project_dir, fname)
        if os.path.exists(path):
            with open(path, "r") as f:
                for line in f:
                    if line.startswith("ANTHROPIC_API_KEY="):
                        return line.split("=", 1)[1].strip().strip('"\'')
    return None


def _extract_code_snippets(project_dir: str, service_name: str) -> list[str]:
    """Find source files for a service and extract relevant snippets."""
    snippets = []
    patterns = [
        # DB queries
        r"\.query\(", r"\.execute\(", r"SELECT ", r"INSERT ", r"UPDATE ",
        r"\.find\(", r"\.findOne\(", r"\.save\(", r"\.create\(",
        # HTTP calls
        r"requests\.", r"axios\.", r"fetch\(", r"http\.get", r"http\.post",
        r"RestTemplate", r"WebClient", r"HttpClient",
        # Cache
        r"redis\.", r"cache\.get", r"cache\.set", r"\.get\(key",
        # Async/queue
        r"kafka\.", r"rabbitmq\.", r"\.publish\(", r"\.send\(",
    ]

    # Search for service directory
    service_dirs = _find_service_dir(project_dir, service_name)

    for svc_dir in service_dirs[:2]:  # limit to 2 dirs
        for root, _, files in os.walk(svc_dir):
            for fname in files:
                if not any(fname.endswith(ext) for ext in
                           [".py", ".js", ".ts", ".java", ".go", ".rb"]):
                    continue
                fpath = os.path.join(root, fname)
                try:
                    with open(fpath, "r", encoding="utf-8", errors="ignore") as f:
                        content = f.read()
                    # Check if file has relevant patterns
                    import re
                    if any(re.search(p, content) for p in patterns):
                        # Extract relevant lines with context
                        lines = content.split("\n")
                        relevant = []
                        for i, line in enumerate(lines):
                            if any(re.search(p, line) for p in patterns):
                                start = max(0, i - 1)
                                end = min(len(lines), i + 3)
                                relevant.extend(lines[start:end])
                                relevant.append("...")
                        if relevant:
                            snippet = f"# {fname}\n" + "\n".join(relevant[:40])
                            snippets.append(snippet)
                except Exception:
                    continue
            if len(snippets) >= 5:
                break
        if len(snippets) >= 5:
            break

    return snippets[:5]


def _find_service_dir(project_dir: str, service_name: str) -> list[str]:
    """Find directories that likely contain the service's source code."""
    candidates = []

    # Direct match
    direct = os.path.join(project_dir, service_name)
    if os.path.isdir(direct):
        candidates.append(direct)

    # Common patterns: src/service_name, services/service_name, apps/service_name
    for prefix in ["src", "services", "apps", "microservices"]:
        path = os.path.join(project_dir, prefix, service_name)
        if os.path.isdir(path):
            candidates.append(path)

    # Fuzzy: any subdir containing service_name
    if not candidates:
        try:
            for entry in os.scandir(project_dir):
                if entry.is_dir() and service_name.lower() in entry.name.lower():
                    candidates.append(entry.path)
        except Exception:
            pass

    return candidates


def _ask_claude(client, service_name: str, snippets: list[str]) -> Optional[dict]:
    """Ask Claude to estimate latency from code snippets."""
    code_context = "\n\n".join(snippets)

    prompt = f"""You are analyzing source code for a microservice called '{service_name}' to estimate its typical request latency.

Here are relevant code snippets showing database queries, HTTP calls, and other I/O operations:

{code_context}

Based on these patterns, estimate the typical p50 and p99 latency for a single request to this service.

Consider:
- Simple DB queries (indexed): p50 ~5ms, p99 ~20ms
- Complex DB queries / joins: p50 ~20ms, p99 ~100ms
- External HTTP calls: p50 ~50ms, p99 ~200ms
- Cache hits: p50 ~1ms, p99 ~5ms
- Multiple sequential DB calls: multiply accordingly
- If async/queue: this service is likely NOT on the synchronous critical path

Respond with ONLY valid JSON, no explanation:
{{"p50_ms": <number>, "p99_ms": <number>, "confidence": "high|medium|low", "reason": "<one sentence>", "is_async": <true|false>}}"""

    try:
        response = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=200,
            messages=[{"role": "user", "content": prompt}]
        )
        text = response.content[0].text.strip()
        # Extract JSON if wrapped in markdown
        if "```" in text:
            text = text.split("```")[1]
            if text.startswith("json"):
                text = text[4:]
        return json.loads(text)
    except Exception:
        return None


def apply_estimates_to_yaml(yaml_content: str, estimates: dict[str, dict]) -> str:
    """Replace TODO latency placeholders in YAML with AI estimates."""
    lines = yaml_content.split("\n")
    result = []
    current_service = None

    for line in lines:
        # Track current service
        if "    - name:" in line:
            current_service = line.split("name:")[-1].strip()

        # Replace TODO latencies
        if current_service and current_service in estimates:
            est = estimates[current_service]
            p50 = est.get("p50_ms", 10)
            p99 = est.get("p99_ms", 50)
            confidence = est.get("confidence", "low")
            reason = est.get("reason", "")

            if "latency_p50:" in line and "TODO" in line:
                line = f"      latency_p50: {p50}ms   # AI estimate ({confidence}): {reason}"
            elif "latency_p99:" in line and "TODO" in line:
                line = f"      latency_p99: {p99}ms   # AI estimate ({confidence})"

        result.append(line)

    return "\n".join(result)
