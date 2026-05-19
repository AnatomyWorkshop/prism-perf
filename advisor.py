"""AI-powered optimization advisor.

Takes an INFEASIBLE verdict and produces:
1. Ranked concrete fixes (with estimated impact)
2. Architectural pattern match
3. Tradeoff analysis for each fix

Requires an OpenAI-compatible API endpoint. Configure via environment:
  PRISM_AI_BASE_URL   — base URL (e.g. https://api.deepseek.com)
  PRISM_AI_KEY        — API key
  PRISM_AI_MODEL      — model name (default: deepseek-chat)

Or set DEEPSEEK_API_KEY / ANTHROPIC_API_KEY for automatic configuration.
"""
import os
import json
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
    # Load .env.keys for local dev (gitignored, never referenced by name in docs)
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

    # Merge environment (takes precedence over file)
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


def get_optimization_advice(topology, verdict, results) -> Optional[dict]:
    """
    Call an AI model to generate ranked optimization advice for an INFEASIBLE topology.

    Returns dict with:
      - fixes: list of {action, impact, effort, tradeoff}
      - pattern: matched architectural pattern name
      - pattern_advice: what the canonical pattern looks like
      - summary: one-paragraph executive summary
    """
    base_url, api_key, model = _load_api_config()

    if not base_url or not api_key:
        return None

    models_to_try = [model]

    # Build context from topology and results
    context = _build_context(topology, results)
    prompt = _build_prompt(context)

    for try_model in models_to_try:
        result = _call_model(base_url, api_key, try_model, prompt)
        if result and "error" not in result:
            return result
        if result and result.get("error", "").startswith("Could not parse"):
            continue

    return result  # return last attempt (may have error)


def _build_context(topology, results) -> str:
    """Summarize topology and violations for the prompt."""
    lines = [f"Topology: {topology.name}"]

    if hasattr(topology, "services") and topology.services:
        svc_list = []
        for s in topology.services:
            name = s.name if hasattr(s, "name") else str(s)
            p99 = getattr(s, "latency_p99", None)
            conns = getattr(s, "connections", None)
            parts = [name]
            if p99:
                parts.append(f"p99={p99}ms")
            if conns:
                parts.append(f"connections={conns}")
            svc_list.append(", ".join(parts))
        lines.append("Services: " + " | ".join(svc_list))

    if hasattr(topology, "chains") and topology.chains:
        for chain in topology.chains:
            if hasattr(chain, "services"):
                lines.append("Chain: " + " → ".join(chain.services))
            elif isinstance(chain, (list, tuple)):
                lines.append("Chain: " + " → ".join(str(s) for s in chain))

    if hasattr(topology, "target"):
        t = topology.target
        p99 = getattr(t, "latency_p99", None)
        qps = getattr(t, "throughput_qps", None)
        if p99:
            lines.append(f"Target: p99 < {p99}ms")
        if qps:
            lines.append(f"Target: throughput {qps} QPS")

    lines.append("")
    lines.append("Violated constraints:")
    for r in results:
        if hasattr(r, "verdict") and r.verdict.value == "INFEASIBLE":
            lines.append(f"  - {r.constraint_name}: {r.evidence[:200]}")

    return "\n".join(lines)


def _call_model(base_url: str, api_key: str, model: str, prompt: str) -> Optional[dict]:
    """Make a single API call and return parsed result."""
    try:
        import urllib.request

        payload = json.dumps({
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": 1200,
            "temperature": 0.3,
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

        with urllib.request.urlopen(req, timeout=60) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            msg = data["choices"][0]["message"]
            text = msg.get("content", "").strip()

            # Deepseek reasoning models put final answer in content
            # but relay may truncate — try reasoning_content as fallback
            if not text:
                rc = msg.get("reasoning_content", "")
                last_brace = rc.rfind("}")
                if last_brace >= 0:
                    depth = 0
                    for i in range(last_brace, -1, -1):
                        if rc[i] == "}":
                            depth += 1
                        elif rc[i] == "{":
                            depth -= 1
                            if depth == 0:
                                text = rc[i:last_brace + 1]
                                break

        return _parse_response(text)

    except Exception as e:
        return {"error": str(e)}


def _build_prompt(context: str) -> str:
    return f"""A performance analysis tool found this microservice topology CANNOT meet its SLA.

{context}

Give optimization advice. Reply in JSON only, no markdown:

{{"pattern":"<name like CQRS/Saga/Async Fan-out/Cache-Aside/Bulkhead>","pattern_advice":"<1 sentence>","fixes":[{{"rank":1,"action":"<specific change>","impact":"<quantified>","effort":"low|medium|high","tradeoff":"<what you lose>"}}],"summary":"<2 sentences for non-technical stakeholder>"}}

Max 4 fixes. Be specific and quantitative. JSON only, no other text."""


def _parse_response(text: str) -> dict:
    """Parse JSON from model response."""
    # Strip markdown code blocks if present
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

    # Try direct parse
    try:
        return json.loads(text)
    except Exception:
        # Try to extract JSON object
        start = text.find("{")
        end = text.rfind("}") + 1
        if start >= 0 and end > start:
            try:
                return json.loads(text[start:end])
            except Exception:
                pass

    return {"raw": text, "error": "Could not parse JSON response"}


def format_advice(advice: dict) -> str:
    """Format optimization advice for terminal output."""
    if not advice or "error" in advice:
        err = advice.get("error", "unknown") if advice else "no response"
        return f"\n  [AI advice unavailable: {err}]"

    lines = ["", "=" * 60, "AI OPTIMIZATION ADVICE", "=" * 60, ""]

    pattern = advice.get("pattern")
    pattern_advice = advice.get("pattern_advice")
    if pattern:
        lines.append(f"ARCHITECTURAL PATTERN: {pattern}")
        if pattern_advice:
            lines.append(f"  {pattern_advice}")
        lines.append("")

    fixes = advice.get("fixes", [])
    if fixes:
        lines.append("RANKED FIXES (by impact/effort):")
        lines.append("")
        for fix in fixes:
            rank = fix.get("rank", "?")
            action = fix.get("action", "")
            impact = fix.get("impact", "")
            effort = fix.get("effort", "?")
            tradeoff = fix.get("tradeoff", "")
            lines.append(f"  {rank}. {action}")
            lines.append(f"     Impact:    {impact}")
            lines.append(f"     Effort:    {effort}")
            lines.append(f"     Tradeoff:  {tradeoff}")
            lines.append("")

    summary = advice.get("summary")
    if summary:
        lines.append("SUMMARY:")
        lines.append(f"  {summary}")
        lines.append("")

    return "\n".join(lines)
