"""Auto-scan project directory for topology sources."""
import os
import re
from typing import Optional


def scan_project(project_dir: str) -> Optional[str]:
    """
    Scan a project directory for known topology sources.
    Returns inferred topology YAML, or None if nothing found.

    Priority:
      1. docker-compose.yml / docker-compose.yaml
      2. Kubernetes manifests (k8s/, deploy/, manifests/)
      3. OpenAPI spec (openapi.yaml, swagger.yaml)
    """
    sources_found = []

    compose = _find_compose(project_dir)
    if compose:
        sources_found.append(("docker-compose", compose))

    k8s = _find_k8s(project_dir)
    if k8s:
        sources_found.append(("kubernetes", k8s))

    openapi = _find_openapi(project_dir)
    if openapi:
        sources_found.append(("openapi", openapi))

    if not sources_found:
        return None

    # Use highest-priority source
    source_type, source_path = sources_found[0]

    if source_type == "docker-compose":
        return _infer_from_compose(source_path)
    elif source_type == "kubernetes":
        return _infer_from_k8s(source_path)
    elif source_type == "openapi":
        return _infer_from_openapi(source_path)

    return None


def _find_compose(project_dir: str) -> Optional[str]:
    for name in ["docker-compose.yml", "docker-compose.yaml",
                 "compose.yml", "compose.yaml"]:
        path = os.path.join(project_dir, name)
        if os.path.exists(path):
            return path
    return None


def _find_k8s(project_dir: str) -> Optional[str]:
    for subdir in ["k8s", "kubernetes", "deploy", "manifests", "infra"]:
        path = os.path.join(project_dir, subdir)
        if os.path.isdir(path):
            return path
    return None


def _find_openapi(project_dir: str) -> Optional[str]:
    for name in ["openapi.yaml", "openapi.yml", "swagger.yaml", "swagger.yml",
                 "api.yaml", "api.yml"]:
        for subdir in [".", "docs", "api", "spec"]:
            path = os.path.join(project_dir, subdir, name)
            if os.path.exists(path):
                return path
    return None


def _infer_from_compose(compose_path: str) -> str:
    """Parse docker-compose.yml and infer topology."""
    try:
        import yaml
        with open(compose_path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
    except ImportError:
        data = _parse_compose_minimal(compose_path)

    services_raw = data.get("services", {})
    if not services_raw:
        return _empty_topology("docker-compose (no services found)")

    services = []
    depends = {}

    for svc_name, svc_cfg in services_raw.items():
        if not isinstance(svc_cfg, dict):
            continue

        # Extract resource hints from deploy.resources
        cpu_limit = None
        mem_limit = None
        deploy = svc_cfg.get("deploy", {})
        if isinstance(deploy, dict):
            resources = deploy.get("resources", {}).get("limits", {})
            cpu_limit = resources.get("cpus")
            mem_limit = resources.get("memory")

        # Extract port hints
        ports = svc_cfg.get("ports", [])
        has_http = any("80" in str(p) or "8080" in str(p) or "3000" in str(p)
                       for p in ports)

        # Skip pure infrastructure services (db, cache, queue)
        svc_type = _classify_service(svc_name, svc_cfg)

        services.append({
            "name": svc_name,
            "type": svc_type,
            "cpu_limit": cpu_limit,
            "mem_limit": mem_limit,
            "has_http": has_http,
        })

        deps = svc_cfg.get("depends_on", [])
        if isinstance(deps, dict):
            deps = list(deps.keys())
        if deps:
            depends[svc_name] = deps

    # Infer call chain: services that depend on others form the chain
    chain = _build_chain_from_deps(services, depends)

    return _generate_topology_yaml(services, chain, source="docker-compose")


def _infer_from_k8s(k8s_dir: str) -> str:
    """Parse Kubernetes manifests and infer topology."""
    deployments = []
    services_map = {}

    for root, _, files in os.walk(k8s_dir):
        for fname in files:
            if not (fname.endswith(".yaml") or fname.endswith(".yml")):
                continue
            fpath = os.path.join(root, fname)
            try:
                docs = _parse_k8s_file(fpath)
                for doc in docs:
                    if not isinstance(doc, dict):
                        continue
                    kind = doc.get("kind", "")
                    name = doc.get("metadata", {}).get("name", "")
                    if kind == "Deployment":
                        deployments.append(_extract_k8s_deployment(name, doc))
                    elif kind == "Service":
                        services_map[name] = doc
            except Exception:
                continue

    if not deployments:
        return _empty_topology("kubernetes (no Deployments found)")

    chain = _infer_k8s_chain(deployments, services_map)
    return _generate_topology_yaml(deployments, chain, source="kubernetes")


def _infer_from_openapi(spec_path: str) -> str:
    """Parse OpenAPI spec and infer service topology from paths."""
    try:
        import yaml
        with open(spec_path, "r", encoding="utf-8") as f:
            spec = yaml.safe_load(f)
    except Exception:
        return _empty_topology("openapi (parse error)")

    title = spec.get("info", {}).get("title", "api")
    paths = spec.get("paths", {})

    # Group paths by first segment → infer service boundaries
    service_groups = {}
    for path in paths:
        parts = [p for p in path.split("/") if p and not p.startswith("{")]
        if parts:
            group = parts[0]
            service_groups.setdefault(group, []).append(path)

    services = [{"name": g, "type": "sync"} for g in service_groups]
    chain = [list(service_groups.keys())]  # flat chain as best guess

    return _generate_topology_yaml(services, chain, source=f"openapi:{title}")


def _classify_service(name: str, cfg: dict) -> str:
    """Classify service as sync/async/db/cache based on name and image."""
    image = cfg.get("image", "").lower()
    name_lower = name.lower()

    infra_keywords = ["postgres", "mysql", "redis", "mongo", "kafka",
                      "rabbitmq", "elasticsearch", "zookeeper", "db", "cache"]
    for kw in infra_keywords:
        if kw in name_lower or kw in image:
            return "infra"

    async_keywords = ["worker", "consumer", "processor", "queue", "job"]
    for kw in async_keywords:
        if kw in name_lower:
            return "async"

    return "sync"


def _build_chain_from_deps(services: list, depends: dict) -> list:
    """Build call chain from depends_on relationships."""
    sync_services = [s["name"] for s in services if s.get("type") == "sync"]
    if not sync_services:
        sync_services = [s["name"] for s in services]

    # Find root (not depended on by anyone)
    all_deps = set()
    for deps in depends.values():
        all_deps.update(deps)

    roots = [s for s in sync_services if s not in all_deps]
    if not roots:
        roots = sync_services[:1]

    # Walk dependency graph
    chain = []
    visited = set()

    def walk(name):
        if name in visited or name not in [s["name"] for s in services]:
            return
        visited.add(name)
        svc = next((s for s in services if s["name"] == name), None)
        if svc and svc.get("type") != "infra":
            chain.append(name)
        for dep in depends.get(name, []):
            walk(dep)

    for root in roots:
        walk(root)

    return [chain] if len(chain) > 1 else []


def _extract_k8s_deployment(name: str, doc: dict) -> dict:
    """Extract relevant fields from a k8s Deployment."""
    spec = doc.get("spec", {})
    replicas = spec.get("replicas", 1)
    containers = spec.get("template", {}).get("spec", {}).get("containers", [])

    cpu_limit = None
    mem_limit = None
    if containers:
        resources = containers[0].get("resources", {}).get("limits", {})
        cpu_limit = resources.get("cpu")
        mem_limit = resources.get("memory")

    return {
        "name": name,
        "type": "sync",
        "replicas": replicas,
        "cpu_limit": cpu_limit,
        "mem_limit": mem_limit,
    }


def _infer_k8s_chain(deployments: list, services_map: dict) -> list:
    """Infer chain from k8s service names (best-effort)."""
    names = [d["name"] for d in deployments]
    if len(names) > 1:
        return [names]
    return []


def _parse_compose_minimal(path: str) -> dict:
    """Minimal YAML parser for docker-compose without PyYAML."""
    services = {}
    current_service = None
    in_services = False

    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            stripped = line.rstrip()
            if stripped == "services:":
                in_services = True
                continue
            if in_services and re.match(r"^  \w", stripped):
                current_service = stripped.strip().rstrip(":")
                services[current_service] = {}
            elif in_services and current_service and "depends_on:" in stripped:
                services[current_service]["depends_on"] = []

    return {"services": services}


def _parse_k8s_file(path: str) -> list:
    """Parse a k8s YAML file (may contain multiple docs)."""
    try:
        import yaml
        with open(path, "r", encoding="utf-8") as f:
            return list(yaml.safe_load_all(f))
    except ImportError:
        return []


def _generate_topology_yaml(services: list, chains: list, source: str) -> str:
    """Generate topology YAML from extracted services and chains."""
    lines = [
        f"# Auto-generated from {source}",
        f"# Review and fill in: target, resources, latency estimates",
        "topology:",
        f'  name: "scanned-topology"',
        "  services:",
    ]

    for svc in services:
        if isinstance(svc, dict) and svc.get("type") == "infra":
            continue
        name = svc["name"] if isinstance(svc, dict) else svc
        svc_type = svc.get("type", "sync") if isinstance(svc, dict) else "sync"
        replicas = svc.get("replicas") if isinstance(svc, dict) else None

        lines.append(f"    - name: {name}")
        lines.append(f"      type: {svc_type}")
        lines.append(f"      latency_p50: 10ms   # TODO: measure or infer from traces")
        lines.append(f"      latency_p99: 50ms   # TODO: measure or infer from traces")
        if replicas and replicas > 1:
            lines.append(f"      parallelism: {replicas}")
            lines.append(f"      serial_fraction: 0.1  # TODO: estimate")

    if chains:
        lines.append("")
        lines.append("  chain:")
        for chain in chains:
            if chain:
                lines.append(f"    - {' -> '.join(chain)}")

    lines.append("")
    lines.append("  resources:")
    lines.append("    connection_pool: 50   # TODO: check your DB/service connection limits")
    lines.append("    network_rtt_internal: 0.5ms")
    lines.append("")
    lines.append("target:")
    lines.append("  latency_p99: 100ms   # TODO: your SLA target")
    lines.append("  throughput: 1000 qps  # TODO: your peak load")
    lines.append("")

    return "\n".join(lines)


def _empty_topology(reason: str) -> str:
    return f"# Could not infer topology from {reason}\n# Try: prism-perf infer <trace.json>\n"
