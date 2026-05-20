"""Topology parser: YAML → structured constraint graph."""
from dataclasses import dataclass, field
from typing import List, Optional, Dict, Any
import re
import yaml


def parse_duration(s: str) -> float:
    """Parse duration string to milliseconds. Accepts '5ms', '1s', '200us'."""
    s = s.strip()
    if s.endswith("ms"):
        return float(s[:-2])
    elif s.endswith("us"):
        return float(s[:-2]) / 1000
    elif s.endswith("s"):
        return float(s[:-1]) * 1000
    else:
        return float(s)


@dataclass
class Service:
    name: str
    type: str = "sync"
    latency_p50: Optional[float] = None
    latency_p99: Optional[float] = None
    latency_per_op: Optional[float] = None
    connections: Optional[int] = None
    parallelism: Optional[int] = None
    serial_fraction: Optional[float] = None
    payload_bytes: Optional[int] = None

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "Service":
        svc = cls(name=d["name"])
        svc.type = d.get("type", "sync")
        if "latency_p50" in d:
            svc.latency_p50 = parse_duration(str(d["latency_p50"]))
        if "latency_p99" in d:
            svc.latency_p99 = parse_duration(str(d["latency_p99"]))
        if "latency_per_op" in d:
            svc.latency_per_op = parse_duration(str(d["latency_per_op"]))
        svc.connections = d.get("connections")
        svc.parallelism = d.get("parallelism")
        svc.serial_fraction = d.get("serial_fraction")
        svc.payload_bytes = d.get("payload_bytes")
        return svc


@dataclass
class ChainStep:
    """A single step in a chain: either a single service name or a parallel fan-out group."""
    services: List[str]  # len=1 for serial, len>1 for parallel fan-out

    @property
    def is_fanout(self) -> bool:
        return len(self.services) > 1

    @property
    def name(self) -> str:
        """Display name for this step."""
        if self.is_fanout:
            return f"[{', '.join(self.services)}]"
        return self.services[0]


@dataclass
class Chain:
    services: List[str]       # flat list of all service names (for backward compat)
    steps: List[ChainStep]    # structured steps with fan-out support

    @classmethod
    def from_str(cls, s: str) -> "Chain":
        raw_steps = [p.strip() for p in re.split(r"\s*->\s*", s.strip())]
        steps = []
        all_services = []
        for step in raw_steps:
            # Fan-out: [svc_a, svc_b] or [svc_a,svc_b]
            if step.startswith("[") and step.endswith("]"):
                names = [n.strip() for n in step[1:-1].split(",")]
                steps.append(ChainStep(services=names))
                all_services.extend(names)
            else:
                steps.append(ChainStep(services=[step]))
                all_services.append(step)
        return cls(services=all_services, steps=steps)


@dataclass
class Resources:
    cpu_cores: Optional[int] = None
    connection_pool: Optional[int] = None
    network_rtt_internal: Optional[float] = None
    network_rtt_external: Optional[float] = None
    bandwidth_mbps: Optional[float] = None
    disk_iops: Optional[int] = None

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "Resources":
        r = cls()
        r.cpu_cores = d.get("cpu_cores")
        r.connection_pool = d.get("connection_pool")
        if "network_rtt_internal" in d:
            r.network_rtt_internal = parse_duration(str(d["network_rtt_internal"]))
        if "network_rtt_external" in d:
            r.network_rtt_external = parse_duration(str(d["network_rtt_external"]))
        r.bandwidth_mbps = d.get("bandwidth_mbps")
        r.disk_iops = d.get("disk_iops")
        return r


@dataclass
class Target:
    latency_p99: Optional[float] = None
    latency_p50: Optional[float] = None
    throughput_qps: Optional[float] = None

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "Target":
        t = cls()
        if "latency_p99" in d:
            t.latency_p99 = parse_duration(str(d["latency_p99"]))
        if "latency_p50" in d:
            t.latency_p50 = parse_duration(str(d["latency_p50"]))
        if "throughput" in d:
            val = str(d["throughput"]).strip().lower()
            if val.endswith("qps"):
                t.throughput_qps = float(val[:-3].strip())
            else:
                t.throughput_qps = float(val)
        return t


@dataclass
class Topology:
    name: str
    services: Dict[str, Service]
    chains: List[Chain]
    resources: Resources
    target: Target

    def get_chain_services(self, chain: Chain) -> List[Service]:
        return [self.services[name] for name in chain.services]


def load_topology(path: str) -> Topology:
    """Load topology from YAML file."""
    with open(path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)

    topo_data = data["topology"]
    services = {}
    for svc_data in topo_data["services"]:
        svc = Service.from_dict(svc_data)
        services[svc.name] = svc

    chains = []
    for chain_str in topo_data.get("chain", []):
        chains.append(Chain.from_str(chain_str))

    resources = Resources.from_dict(topo_data.get("resources", {}))
    target = Target.from_dict(data.get("target", {}))

    return Topology(
        name=topo_data.get("name", "unnamed"),
        services=services,
        chains=chains,
        resources=resources,
        target=target,
    )
