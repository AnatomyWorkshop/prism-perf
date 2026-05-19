"""AI-assisted latency estimation from source code analysis.

Reads service source code and estimates p50/p99 latencies based on detected
patterns: DB queries, HTTP calls, cache operations, etc.

This fills in the TODO latencies that scan produces, making the workflow
truly zero-config for projects with readable source code.

Configure via environment (same as advisor.py):
  PRISM_AI_BASE_URL   — base URL
  PRISM_AI_KEY        — API key
  PRISM_AI_MODEL      — model name (default: deepseek-chat)

Or set DEEPSEEK_API_KEY / ANTHROPIC_API_KEY for automatic configuration.
"""
import os
import json
import re
from typing import Optional


def _load_api_config() -> tuple[str, str, str]:
    """
    Return (base_url, api_key, model) from environment or .env.keys.

    Priority:
      1. PRISM_AI_* env vars (explicit user config)
      2. DEEPSEEK_API_KEY (official Deepseek)
      3. ANTHROPIC_API_KEY (official Anthropic)
      4. .env.keys file (local dev convenience — not documented publicly)
    """
    raw = {}
    for search_dir in [
        os.path.dirname(os.path.abspath(__file__)),
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    ]:
        env_path = os.path.join(search_dir, ".env.keys")
        if os.path.exists(env_path):
            with open(env_path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith("#") and "=" in line:
                        k, v = line.split("=", 1)
                        raw[k.strip()] = v.strip().strip('"\'')
            break

    for k in list(raw):
        if os.environ.get(k):
            raw[k] = os.environ[k]

    # 1. Explicit user config
    if raw.get("PRISM_AI_KEY") and raw.get("PRISM_AI_BASE_URL"):
        return (
            raw["PRISM_AI_BASE_URL"].rstrip("/"),
            raw["PRISM_AI_KEY"],
            raw.get("PRISM_AI_MODEL", "deepseek-chat"),
        )

    # 2. Official Deepseek
    if raw.get("DEEPSEEK_API_KEY"):
        base = raw.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com").rstrip("/")
        return (base, raw["DEEPSEEK_API_KEY"], "deepseek-chat")

    # 3. Official Anthropic
    if raw.get("ANTHROPIC_API_KEY"):
        base = raw.get("ANTHROPIC_BASE_URL", "https://api.anthropic.com").rstrip("/")
        return (base, raw["ANTHROPIC_API_KEY"], "claude-haiku-4-5-20251001")

    # 4. Internal fallback from .env.keys (local dev only)
    for url_key, key_key, model in [
        ("RELAY_LANYI_BASE_URL", "RELAY_LANYI_KEY", "claude-haiku-4-5-20251001"),
        ("RELAY_SPACECX_BASE_URL", "RELAY_SPACECX_KEY", "deepseek-chat"),
    ]:
        if raw.get(key_key) and raw.get(url_key):
            return (raw[url_key].rstrip("/"), raw[key_key], model)

    return ("", "", "")


def estimate_latencies(project_dir: str, service_names: list[str]) -> dict[str, dict]:
    """
    Analyze source code for each service and estimate latencies.

    Returns: {service_name: {"p50": float_ms, "p99": float_ms, "confidence": str, "reason": str}}
    """
    base_url, api_key, model = _load_api_config()
    if not base_url or not api_key:
        return {}

    results = {}
    for service_name in service_names:
        code_snippets = _extract_code_snippets(project_dir, service_name)
        if not code_snippets:
            continue
        estimate = _ask_model(base_url, api_key, model, service_name, code_snippets)
        if estimate:
            results[service_name] = estimate

    return results


def _extract_code_snippets(project_dir: str, service_name: str) -> list[str]:
    """Find source files for a service and extract relevant snippets."""
    snippets = []
    patterns = [
        r"\.query\(", r"\.execute\(", r"SELECT ", r"INSERT ", r"UPDATE ",
        r"\.find\(", r"\.findOne\(", r"\.save\(", r"\.create\(",
        r"requests\.", r"axios\.", r"fetch\(", r"http\.get", r"http\.post",
        r"RestTemplate", r"WebClient", r"HttpClient",
        r"redis\.", r"cache\.get", r"cache\.set", r"\.get\(key",
        r"kafka\.", r"rabbitmq\.", r"\.publish\(", r"\.send\(",
    ]

    service_dirs = _find_service_dir(project_dir, service_name)

    for svc_dir in service_dirs[:2]:
        for root, _, files in os.walk(svc_dir):
            for fname in files:
                if not any(fname.endswith(ext) for ext in
                           [".py", ".js", ".ts", ".java", ".go", ".rb"]):
                    continue
                fpath = os.path.join(root, fname)
                try:
                    with open(fpath, "r", encoding="utf-8", errors="ignore") as f:
                        content = f.read()
                    if any(re.search(p, content) for p in patterns):
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

    direct = os.path.join(project_dir, service_name)
    if os.path.isdir(direct):
        candidates.append(direct)

    for prefix in ["src", "services", "apps", "microservices"]:
        path = os.path.join(project_dir, prefix, service_name)
        if os.path.isdir(path):
            candidates.append(path)

    if not candidates:
        try:
            for entry in os.scandir(project_dir):
                if entry.is_dir() and service_name.lower() in entry.name.lower():
                    candidates.append(entry.path)
        except Exception:
            pass

    return candidates


def _ask_model(base_url: str, api_key: str, model: str,
               service_name: str, snippets: list[str]) -> Optional[dict]:
    """Ask the model to estimate latency from code snippets."""
    import urllib.request

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
        payload = json.dumps({
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": 200,
            "temperature": 0.2,
        }).encode("utf-8")

        req = urllib.request.Request(
            f"{base_url}/v1/chat/completions",
            data=payload,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {api_key}",
            },
            method="POST",
        )

        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            text = data["choices"][0]["message"].get("content", "").strip()

        return _parse_response(text)

    except Exception:
        return None


def _parse_response(text: str) -> Optional[dict]:
    """Parse JSON from model response."""
    if "```" in text:
        parts = text.split("```")
        for part in parts:
            part = part.strip()
            if part.startswith("json"):
                part = part[4:].strip()
            try:
                return json.loads(part)
            except Exception:
                continue

    try:
        return json.loads(text)
    except Exception:
        start = text.find("{")
        end = text.rfind("}") + 1
        if start >= 0 and end > start:
            try:
                return json.loads(text[start:end])
            except Exception:
                pass

    return None


def apply_estimates_to_yaml(yaml_content: str, estimates: dict[str, dict]) -> str:
    """Replace TODO latency placeholders in YAML with AI estimates."""
    lines = yaml_content.split("\n")
    result = []
    current_service = None

    for line in lines:
        if "    - name:" in line:
            current_service = line.split("name:")[-1].strip()

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
