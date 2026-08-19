"""Attack and incident scenarios injected into the synthetic stream.

Every scenario carries a ``label`` that is stamped onto the events it produces
or mutates.  That label never reaches the detectors — it is written to
``attack_label`` purely so the evaluation harness can measure precision/recall
against ground truth.

A scenario expresses its effect through three optional channels:

``rate_multiplier``
    scales organic traffic (flash sale, marketing spike, outage-driven drop)
``attacker``
    injects an additional, independently-shaped stream of requests
``mutation``
    rewrites organic responses (error injection, latency degradation)
"""

from __future__ import annotations

import math
import random
import uuid
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Sequence

from .catalog import ATTACK_TOOLS, HOSTILE_ZONES, SCAN_PATHS


@dataclass
class AttackerProfile:
    """An injected request stream that does not follow the organic model."""

    ip_count: int
    rps_per_ip: float
    paths: Sequence[str]
    method: str = "GET"
    path_mode: str = "fixed"  # fixed | scan | paginate
    status_profile: Sequence[tuple] = ((200, 1.0),)
    ua_pool: Sequence[str] = tuple(ATTACK_TOOLS)
    bytes_mean: int = 4_000
    lat_mu: float = 4.6
    lat_sigma: float = 0.6
    service: str = "api-gateway"
    hostile_geo: bool = True
    reuse_session: bool = False


@dataclass
class Mutation:
    """Rewrites organic events, simulating a failing or degraded dependency."""

    target_service: Optional[str] = None
    target_endpoint: Optional[str] = None
    affected_fraction: float = 0.5
    error_statuses: Sequence[tuple] = ()
    latency_multiplier: float = 1.0
    latency_jitter: float = 0.0


@dataclass
class Scenario:
    scenario_id: str
    type: str
    label: str
    description: str
    started_at: float
    duration: float
    intensity: float = 1.0
    rate_multiplier: float = 1.0
    attacker: Optional[AttackerProfile] = None
    mutation: Optional[Mutation] = None
    source: str = "auto"
    ramp_fraction: float = 0.18

    def active(self, now: float) -> bool:
        return self.started_at <= now < self.started_at + self.duration

    def progress(self, now: float) -> float:
        return min(max((now - self.started_at) / max(self.duration, 1e-6), 0.0), 1.0)

    def envelope(self, now: float) -> float:
        """Trapezoidal ramp-up/plateau/ramp-down.

        Instantaneous step changes are trivially detectable; real incidents
        ramp.  This keeps the detection problem honest.
        """
        p = self.progress(now)
        ramp = max(self.ramp_fraction, 1e-6)
        if p < ramp:
            shape = 0.5 - 0.5 * math.cos(math.pi * (p / ramp))
        elif p > 1.0 - ramp:
            shape = 0.5 - 0.5 * math.cos(math.pi * ((1.0 - p) / ramp))
        else:
            shape = 1.0
        return shape * self.intensity

    def to_dict(self) -> Dict[str, Any]:
        return {
            "scenario_id": self.scenario_id,
            "type": self.type,
            "label": self.label,
            "description": self.description,
            "started_at": self.started_at,
            "ends_at": self.started_at + self.duration,
            "duration": self.duration,
            "intensity": self.intensity,
            "source": self.source,
        }


def _sid(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:8]}"


# ── Factories ────────────────────────────────────────────────────────────────
def traffic_spike(now: float, rng: random.Random, intensity: float = 1.0, duration: Optional[float] = None) -> Scenario:
    return Scenario(
        scenario_id=_sid("spike"),
        type="traffic_spike",
        label="traffic_spike",
        description="Marketing campaign / flash sale: organic traffic multiplies across all entry points.",
        started_at=now,
        duration=duration or rng.uniform(240, 600),
        intensity=intensity,
        rate_multiplier=rng.uniform(2.8, 5.5),
    )


def ddos_burst(now: float, rng: random.Random, intensity: float = 1.0, duration: Optional[float] = None) -> Scenario:
    return Scenario(
        scenario_id=_sid("ddos"),
        type="ddos_burst",
        label="ddos_burst",
        description="Volumetric flood from a small botnet against a handful of expensive endpoints.",
        started_at=now,
        duration=duration or rng.uniform(180, 420),
        intensity=intensity,
        attacker=AttackerProfile(
            ip_count=rng.randint(6, 28),
            rps_per_ip=rng.uniform(18, 46),
            paths=["/api/v1/search", "/api/v1/recommendations", "/api/v1/products"],
            method="GET",
            status_profile=((200, 0.45), (429, 0.18), (503, 0.27), (504, 0.10)),
            bytes_mean=2_400,
            lat_mu=6.2,
            lat_sigma=0.8,
            service="search-svc",
        ),
        mutation=Mutation(
            target_service="search-svc",
            affected_fraction=0.35,
            error_statuses=((503, 0.6), (504, 0.4)),
            latency_multiplier=3.4,
            latency_jitter=0.5,
        ),
        ramp_fraction=0.08,
    )


def error_spike(now: float, rng: random.Random, intensity: float = 1.0, duration: Optional[float] = None) -> Scenario:
    service = rng.choice(["checkout-svc", "api-gateway", "search-svc"])
    return Scenario(
        scenario_id=_sid("errspike"),
        type="error_spike",
        label="error_spike",
        description=f"Bad deploy on {service}: a large fraction of requests start returning 5xx.",
        started_at=now,
        duration=duration or rng.uniform(180, 480),
        intensity=intensity,
        mutation=Mutation(
            target_service=service,
            affected_fraction=rng.uniform(0.28, 0.62),
            error_statuses=((500, 0.55), (502, 0.25), (503, 0.20)),
            latency_multiplier=1.4,
        ),
    )


def latency_degradation(now: float, rng: random.Random, intensity: float = 1.0, duration: Optional[float] = None) -> Scenario:
    endpoint = rng.choice(["/api/v1/search", "/api/v1/checkout", "/api/v1/products", "/api/v1/payments"])
    return Scenario(
        scenario_id=_sid("slow"),
        type="latency_degradation",
        label="latency_degradation",
        description=f"Database contention makes {endpoint} several times slower without failing.",
        started_at=now,
        duration=duration or rng.uniform(300, 720),
        intensity=intensity,
        mutation=Mutation(
            target_endpoint=endpoint,
            affected_fraction=rng.uniform(0.6, 0.95),
            latency_multiplier=rng.uniform(4.0, 9.0),
            latency_jitter=0.6,
            error_statuses=((504, 0.04),),
        ),
        ramp_fraction=0.25,
    )


def credential_stuffing(now: float, rng: random.Random, intensity: float = 1.0, duration: Optional[float] = None) -> Scenario:
    return Scenario(
        scenario_id=_sid("stuff"),
        type="credential_stuffing",
        label="credential_stuffing",
        description="Distributed login attempts replaying a breached credential list.",
        started_at=now,
        duration=duration or rng.uniform(240, 600),
        intensity=intensity,
        attacker=AttackerProfile(
            ip_count=rng.randint(3, 12),
            rps_per_ip=rng.uniform(1.2, 4.5),
            paths=["/api/v1/auth/login"],
            method="POST",
            status_profile=((401, 0.88), (403, 0.06), (200, 0.03), (429, 0.03)),
            bytes_mean=900,
            lat_mu=5.2,
            lat_sigma=0.4,
            service="api-gateway",
        ),
    )


def endpoint_scan(now: float, rng: random.Random, intensity: float = 1.0, duration: Optional[float] = None) -> Scenario:
    return Scenario(
        scenario_id=_sid("scan"),
        type="endpoint_scan",
        label="endpoint_scan",
        description="Vulnerability scanner enumerating well-known admin and config paths.",
        started_at=now,
        duration=duration or rng.uniform(150, 400),
        intensity=intensity,
        attacker=AttackerProfile(
            ip_count=rng.randint(1, 4),
            rps_per_ip=rng.uniform(4.0, 14.0),
            paths=SCAN_PATHS,
            path_mode="scan",
            method="GET",
            status_profile=((404, 0.82), (403, 0.11), (400, 0.04), (200, 0.03)),
            bytes_mean=600,
            lat_mu=3.4,
            lat_sigma=0.5,
        ),
    )


def data_scraping(now: float, rng: random.Random, intensity: float = 1.0, duration: Optional[float] = None) -> Scenario:
    return Scenario(
        scenario_id=_sid("scrape"),
        type="data_scraping",
        label="data_scraping",
        description="Single client paginating the entire product catalog at machine speed.",
        started_at=now,
        duration=duration or rng.uniform(300, 900),
        intensity=intensity,
        attacker=AttackerProfile(
            ip_count=rng.randint(1, 3),
            rps_per_ip=rng.uniform(6.0, 18.0),
            paths=["/api/v1/products"],
            path_mode="paginate",
            method="GET",
            status_profile=((200, 0.96), (429, 0.04)),
            bytes_mean=48_000,
            lat_mu=4.3,
            lat_sigma=0.35,
            reuse_session=True,
        ),
    )


def bot_surge(now: float, rng: random.Random, intensity: float = 1.0, duration: Optional[float] = None) -> Scenario:
    return Scenario(
        scenario_id=_sid("bots"),
        type="bot_surge",
        label="bot_surge",
        description="Aggressive crawler re-indexing the whole catalog.",
        started_at=now,
        duration=duration or rng.uniform(240, 600),
        intensity=intensity,
        attacker=AttackerProfile(
            ip_count=rng.randint(4, 14),
            rps_per_ip=rng.uniform(2.0, 7.0),
            paths=["/products/{id}", "/category/{slug}", "/blog/{slug}", "/api/v1/products"],
            method="GET",
            status_profile=((200, 0.93), (404, 0.05), (429, 0.02)),
            ua_pool=(
                "Mozilla/5.0 (compatible; AhrefsBot/7.0; +http://ahrefs.com/robot/)",
                "Mozilla/5.0 (compatible; SemrushBot/7~bl; +http://www.semrush.com/bot.html)",
                "Mozilla/5.0 (compatible; DataForSeoBot/1.0; +https://dataforseo.com/dataforseo-bot)",
            ),
            bytes_mean=36_000,
            lat_mu=4.0,
            lat_sigma=0.5,
            hostile_geo=False,
            service="web-storefront",
        ),
    )


def service_outage(now: float, rng: random.Random, intensity: float = 1.0, duration: Optional[float] = None) -> Scenario:
    service = rng.choice(["checkout-svc", "search-svc"])
    return Scenario(
        scenario_id=_sid("outage"),
        type="service_outage",
        label="service_outage",
        description=f"{service} is hard-down: nearly every request to it returns 503 and users drop off.",
        started_at=now,
        duration=duration or rng.uniform(180, 420),
        intensity=intensity,
        rate_multiplier=0.72,
        mutation=Mutation(
            target_service=service,
            affected_fraction=0.93,
            error_statuses=((503, 0.82), (502, 0.18)),
            latency_multiplier=0.35,
        ),
        ramp_fraction=0.06,
    )


def geo_shift(now: float, rng: random.Random, intensity: float = 1.0, duration: Optional[float] = None) -> Scenario:
    zone = rng.choice(HOSTILE_ZONES)
    return Scenario(
        scenario_id=_sid("geo"),
        type="geo_shift",
        label="geo_shift",
        description=f"Sudden traffic surge originating from {zone.name}, unlike the usual geographic mix.",
        started_at=now,
        duration=duration or rng.uniform(240, 600),
        intensity=intensity,
        attacker=AttackerProfile(
            ip_count=rng.randint(25, 90),
            rps_per_ip=rng.uniform(0.6, 2.4),
            paths=["/", "/category/{slug}", "/api/v1/products", "/api/v1/search"],
            method="GET",
            status_profile=((200, 0.9), (404, 0.06), (403, 0.04)),
            bytes_mean=22_000,
            lat_mu=5.4,
            lat_sigma=0.6,
            service="web-storefront",
        ),
    )


FACTORIES = {
    "traffic_spike": traffic_spike,
    "ddos_burst": ddos_burst,
    "error_spike": error_spike,
    "latency_degradation": latency_degradation,
    "credential_stuffing": credential_stuffing,
    "endpoint_scan": endpoint_scan,
    "data_scraping": data_scraping,
    "bot_surge": bot_surge,
    "service_outage": service_outage,
    "geo_shift": geo_shift,
}

#: Relative likelihood when the scheduler picks a scenario on its own.
AUTO_WEIGHTS = {
    "traffic_spike": 14.0,
    "ddos_burst": 10.0,
    "error_spike": 13.0,
    "latency_degradation": 13.0,
    "credential_stuffing": 11.0,
    "endpoint_scan": 12.0,
    "data_scraping": 9.0,
    "bot_surge": 8.0,
    "service_outage": 5.0,
    "geo_shift": 5.0,
}

CATALOG_INFO = [
    {
        "type": name,
        "label": name.replace("_", " ").title(),
        "description": FACTORIES[name](0.0, random.Random(7)).description,
        "weight": AUTO_WEIGHTS[name],
    }
    for name in FACTORIES
]


class ScenarioScheduler:
    """Decides when incidents happen and keeps the active set.

    Two sources: an automatic Poisson-ish scheduler (so an unattended demo keeps
    producing interesting data), and on-demand injection from the dashboard.
    """

    def __init__(
        self,
        rng: random.Random,
        enabled: bool = True,
        mean_gap_seconds: float = 210.0,
        max_concurrent: int = 3,
    ):
        self.rng = rng
        self.enabled = enabled
        self.mean_gap_seconds = mean_gap_seconds
        self.max_concurrent = max_concurrent
        self.active: List[Scenario] = []
        self.history: List[Scenario] = []
        self._next_auto: Optional[float] = None

    def tick(self, now: float) -> List[Scenario]:
        """Expire finished scenarios and maybe start a new one.  Returns started."""
        finished = [s for s in self.active if not s.active(now)]
        if finished:
            self.active = [s for s in self.active if s.active(now)]
            self.history.extend(finished)
            self.history = self.history[-200:]

        started: List[Scenario] = []
        if not self.enabled:
            return started
        if self._next_auto is None:
            self._next_auto = now + self.rng.expovariate(1.0 / self.mean_gap_seconds)
        if now >= self._next_auto and len(self.active) < self.max_concurrent:
            scenario = self.spawn(now)
            if scenario:
                started.append(scenario)
            self._next_auto = now + self.rng.expovariate(1.0 / self.mean_gap_seconds)
        return started

    def spawn(self, now: float, type_: Optional[str] = None, intensity: Optional[float] = None,
              duration: Optional[float] = None, source: str = "auto") -> Optional[Scenario]:
        if type_ is None:
            names = list(AUTO_WEIGHTS)
            weights = [AUTO_WEIGHTS[n] for n in names]
            # Avoid stacking two of the same kind.
            running = {s.type for s in self.active}
            pool = [(n, w) for n, w in zip(names, weights) if n not in running]
            if not pool:
                return None
            names, weights = zip(*pool)
            type_ = self.rng.choices(names, weights=weights, k=1)[0]
        factory = FACTORIES.get(type_)
        if factory is None:
            return None
        scenario = factory(now, self.rng, intensity if intensity is not None else self.rng.uniform(0.75, 1.3), duration)
        scenario.source = source
        self.active.append(scenario)
        return scenario

    def rate_multiplier(self, now: float) -> float:
        multiplier = 1.0
        for scenario in self.active:
            if scenario.rate_multiplier != 1.0:
                envelope = scenario.envelope(now)
                multiplier *= 1.0 + (scenario.rate_multiplier - 1.0) * envelope
        return multiplier

    def mutations(self, now: float) -> List[tuple]:
        return [(s, s.mutation, s.envelope(now)) for s in self.active if s.mutation is not None]

    def attackers(self, now: float) -> List[tuple]:
        return [(s, s.attacker, s.envelope(now)) for s in self.active if s.attacker is not None]

    def snapshot(self, now: float) -> Dict[str, Any]:
        return {
            "active": [dict(s.to_dict(), envelope=round(s.envelope(now), 3), progress=round(s.progress(now), 3))
                       for s in self.active],
            "recent": [s.to_dict() for s in self.history[-10:]],
            "enabled": self.enabled,
        }
