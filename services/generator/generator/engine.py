"""The traffic model: turns simulated time into realistic log events.

Layers, from the outside in:

1. **Volume** — a diurnal sine profile, a weekday/weekend factor and a slow
   mean-reverting (Ornstein-Uhlenbeck) wander, sampled with Poisson arrivals.
2. **Sessions** — a pool of users whose next request is drawn from a Markov
   chain over endpoint groups, so paths correlate the way real journeys do.
3. **Response** — status and latency are drawn per endpoint, then inflated by a
   queueing term when the instantaneous rate exceeds that endpoint's capacity.
   Latency therefore *correlates with load*, which is what makes latency
   anomalies interesting instead of independent noise.
4. **Scenarios** — injected attacker streams and response mutations.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Sequence, Tuple

from .catalog import (
    BLOG_SLUGS,
    BOTS,
    BROWSERS,
    CATEGORY_SLUGS,
    ENDPOINTS,
    ENDPOINTS_BY_GROUP,
    GEO_ZONES,
    HOSTILE_ZONES,
    HOSTS_PER_SERVICE,
    JOURNEY,
    REFERRERS,
    REGIONS,
    STATUS_BY_INTENT,
    Endpoint,
    GeoZone,
)
from .scenarios import AttackerProfile, Mutation, Scenario, ScenarioScheduler

SCHEMA_VERSION = 1


def _weighted(rng: random.Random, items: Sequence, weights: Sequence[float]):
    return rng.choices(items, weights=weights, k=1)[0]


def _hexid(rng: random.Random, size: int = 16) -> str:
    return f"{rng.getrandbits(size * 4):0{size}x}"


@dataclass
class Client:
    """A returning visitor: stable identity, geography and device."""

    user_id: Optional[str]
    ip: str
    zone: GeoZone
    user_agent: str
    browser: str
    os: str
    device: str
    is_bot: bool = False


@dataclass
class Session:
    session_id: str
    client: Client
    group: str
    started_at: float
    last_seen: float
    requests: int = 0
    referrer: str = "-"

    def expired(self, now: float, ttl: float = 900.0) -> bool:
        return now - self.last_seen > ttl


class TrafficEngine:
    def __init__(
        self,
        rng: random.Random,
        base_rps: float = 180.0,
        scheduler: Optional[ScenarioScheduler] = None,
        user_pool_size: int = 12_000,
        bot_share: float = 0.06,
    ):
        self.rng = rng
        self.base_rps = base_rps
        self.scheduler = scheduler
        self.bot_share = bot_share

        self._endpoint_weights = [e.weight for e in ENDPOINTS]
        self._zone_weights = [z.weight for z in GEO_ZONES]
        self._browser_weights = [b[4] for b in BROWSERS]
        self._bot_weights = [b[2] for b in BOTS]
        self._referrer_weights = [r[1] for r in REFERRERS]

        self.clients: List[Client] = [self._make_client(i) for i in range(user_pool_size)]
        self.sessions: Dict[str, Session] = {}
        self._session_ids: List[str] = []

        #: Ornstein-Uhlenbeck state for smooth, non-repeating demand wander.
        self._ou = 0.0
        #: Rolling per-endpoint request counts used for the queueing term.
        self._recent_load: Dict[str, float] = {}
        #: Attacker IPs are stable for the lifetime of a scenario.
        self._attack_clients: Dict[str, List[Client]] = {}
        self._scrape_cursor: Dict[str, int] = {}
        self.stats: Dict[str, int] = {"organic": 0, "attack": 0, "mutated": 0}

    # ── Identity ─────────────────────────────────────────────────────────────
    def _ip_from_zone(self, zone: GeoZone) -> str:
        prefix = self.rng.choice(zone.prefixes)
        octets = prefix.split(".")
        while len(octets) < 4:
            octets.append(str(self.rng.randint(1, 254)))
        return ".".join(octets[:4])

    def _make_client(self, index: int, zone: Optional[GeoZone] = None, force_bot: bool = False,
                     ua_pool: Optional[Sequence[str]] = None) -> Client:
        zone = zone or _weighted(self.rng, GEO_ZONES, self._zone_weights)
        is_bot = force_bot or self.rng.random() < self.bot_share
        if ua_pool:
            user_agent = self.rng.choice(list(ua_pool))
            browser, os_name, device = "unknown", "unknown", "bot"
        elif is_bot:
            name, user_agent, _ = _weighted(self.rng, BOTS, self._bot_weights)
            browser, os_name, device = name, "unknown", "bot"
        else:
            browser, os_name, device, user_agent, _ = _weighted(self.rng, BROWSERS, self._browser_weights)
        return Client(
            user_id=None if (is_bot or self.rng.random() < 0.45) else f"u_{index:06d}",
            ip=self._ip_from_zone(zone),
            zone=zone,
            user_agent=user_agent,
            browser=browser,
            os=os_name,
            device=device,
            is_bot=is_bot or bool(ua_pool),
        )

    # ── Demand model ─────────────────────────────────────────────────────────
    def target_rps(self, ts: float) -> float:
        moment = datetime.fromtimestamp(ts, tz=timezone.utc)
        hour = moment.hour + moment.minute / 60.0

        # Two-peak daily shape (EU morning, US afternoon), floor at ~28% of peak.
        primary = math.sin(math.pi * max(0.0, min(1.0, (hour - 5.0) / 15.0)))
        secondary = 0.55 * math.exp(-((hour - 20.0) ** 2) / 6.0)
        diurnal = 0.28 + 0.72 * max(primary, 0.0) + secondary

        weekday = moment.weekday()
        week_factor = 0.82 if weekday >= 5 else 1.0
        if weekday == 0:
            week_factor = 1.06

        # OU process: mean-reverting noise, so demand wanders instead of jittering.
        self._ou += (-0.12 * self._ou + self.rng.gauss(0, 1) * 0.09)
        self._ou = max(-0.45, min(0.45, self._ou))

        multiplier = self.scheduler.rate_multiplier(ts) if self.scheduler else 1.0
        return max(self.base_rps * diurnal * week_factor * (1.0 + self._ou) * multiplier, 1.0)

    # ── Sessions ─────────────────────────────────────────────────────────────
    def _prune_sessions(self, now: float) -> None:
        if len(self._session_ids) < 4000:
            return
        keep: List[str] = []
        for session_id in self._session_ids:
            session = self.sessions.get(session_id)
            if session and not session.expired(now):
                keep.append(session_id)
            else:
                self.sessions.pop(session_id, None)
        self._session_ids = keep[-6000:]

    def _new_session(self, now: float) -> Session:
        client = self.rng.choice(self.clients)
        session = Session(
            session_id=f"s_{_hexid(self.rng, 12)}",
            client=client,
            group=_weighted(self.rng, list(JOURNEY["__start__"]), list(JOURNEY["__start__"].values())),
            started_at=now,
            last_seen=now,
            referrer=_weighted(self.rng, [r[0] for r in REFERRERS], self._referrer_weights),
        )
        self.sessions[session.session_id] = session
        self._session_ids.append(session.session_id)
        return session

    def _pick_session(self, now: float) -> Session:
        # Bias towards recent sessions so journeys stay coherent, but always keep
        # a healthy arrival rate of new visitors.
        if self._session_ids and self.rng.random() > 0.28:
            for _ in range(4):
                session_id = self._session_ids[-self.rng.randint(1, min(len(self._session_ids), 900))]
                session = self.sessions.get(session_id)
                if session and not session.expired(now):
                    return session
        return self._new_session(now)

    def _advance(self, session: Session) -> str:
        transitions = JOURNEY.get(session.group, JOURNEY["__start__"])
        groups = list(transitions)
        nxt = _weighted(self.rng, groups, [transitions[g] for g in groups])
        if nxt == "__end__":
            session.last_seen = 0.0  # force expiry
            return session.group
        session.group = nxt
        return nxt

    # ── Response model ───────────────────────────────────────────────────────
    def _materialize(self, endpoint: Endpoint) -> str:
        path = endpoint.path
        if "{id}" in path:
            path = path.replace("{id}", str(self.rng.randint(1000, 98_999)))
        if "{slug}" in path:
            pool = BLOG_SLUGS if path.startswith("/blog") else CATEGORY_SLUGS
            path = path.replace("{slug}", self.rng.choice(pool))
        if "{hash}" in path:
            path = path.replace("{hash}", _hexid(self.rng, 8))
        return path

    def _load_factor(self, endpoint: Endpoint, rps: float) -> float:
        """Queueing inflation: latency climbs once demand passes capacity."""
        endpoint_rps = rps * (endpoint.weight / max(sum(self._endpoint_weights), 1.0))
        utilisation = endpoint_rps / max(endpoint.capacity_rps, 1.0)
        if utilisation <= 0.7:
            return 1.0
        return 1.0 + 2.6 * ((utilisation - 0.7) ** 1.6)

    def _status_for(self, endpoint: Endpoint) -> Tuple[int, str]:
        roll = self.rng.random()
        if roll < endpoint.err_5xx:
            statuses = STATUS_BY_INTENT["server_error"]
        elif roll < endpoint.err_5xx + endpoint.err_4xx:
            statuses = STATUS_BY_INTENT["client_error"]
        else:
            statuses = STATUS_BY_INTENT["success"]
        codes = [s[0] for s in statuses]
        weights = [s[1] for s in statuses]
        return _weighted(self.rng, codes, weights), ("error" if statuses is not STATUS_BY_INTENT["success"] else "ok")

    def _latency(self, endpoint: Endpoint, zone: GeoZone, cache_hit: bool, rps: float) -> Tuple[float, float]:
        upstream = self.rng.lognormvariate(endpoint.lat_mu, endpoint.lat_sigma)
        if cache_hit:
            upstream *= 0.18
        upstream *= self._load_factor(endpoint, rps)
        network = 12.0 * zone.rtt_factor + self.rng.expovariate(1 / 8.0)
        return round(upstream + network, 2), round(upstream, 2)

    # ── Event construction ───────────────────────────────────────────────────
    def _base_event(self, ts: float) -> Dict[str, Any]:
        moment = datetime.fromtimestamp(ts, tz=timezone.utc)
        return {
            "event_id": _hexid(self.rng, 24),
            "schema_version": SCHEMA_VERSION,
            "timestamp": moment.isoformat().replace("+00:00", "Z"),
            "ingest_ts": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "request_id": _hexid(self.rng, 16),
            "protocol": "HTTP/2.0" if self.rng.random() < 0.72 else "HTTP/1.1",
            "tls_version": "TLSv1.3" if self.rng.random() < 0.88 else "TLSv1.2",
            "region": self.rng.choice(REGIONS),
            "attack_label": None,
        }

    def organic_event(self, ts: float, rps: float) -> Dict[str, Any]:
        session = self._pick_session(ts)
        group = self._advance(session)
        candidates = ENDPOINTS_BY_GROUP.get(group) or ENDPOINTS
        endpoint = _weighted(self.rng, candidates, [e.weight for e in candidates])

        client = session.client
        session.requests += 1
        session.last_seen = max(session.last_seen, ts)

        cache_hit = endpoint.cacheable and self.rng.random() < 0.74
        status, _ = self._status_for(endpoint)
        # Unauthenticated users hitting protected routes get 401s more often.
        if endpoint.auth_required and client.user_id is None and self.rng.random() < 0.22:
            status = 401
        response_time, upstream = self._latency(endpoint, client.zone, cache_hit, rps)
        bytes_sent = 0 if status in (204, 304) else max(120, int(self.rng.lognormvariate(math.log(max(endpoint.bytes_mean, 200)), 0.55)))

        event = self._base_event(ts)
        event.update(
            {
                "service": endpoint.service,
                "host": self.rng.choice(HOSTS_PER_SERVICE.get(endpoint.service, ["web-01"])),
                "ip": client.ip,
                "method": endpoint.method,
                "path": self._materialize(endpoint),
                "endpoint": endpoint.path,
                "query": "" if not endpoint.dynamic else self.rng.choice(["", "", "", "?ref=nav", "?page=2", "?utm_source=email"]),
                "status": status,
                "bytes_sent": bytes_sent,
                "bytes_received": self.rng.randint(120, 2200) if endpoint.method in ("POST", "PUT", "PATCH") else self.rng.randint(60, 400),
                "response_time_ms": response_time,
                "upstream_time_ms": upstream,
                "referrer": session.referrer,
                "user_agent": client.user_agent,
                "user_id": client.user_id,
                "session_id": session.session_id,
                "country": client.zone.code,
                "country_name": client.zone.name,
                "city": client.zone.city,
                "lat": client.zone.lat,
                "lon": client.zone.lon,
                "asn": client.zone.asn,
                "org": client.zone.org,
                "device": client.device,
                "browser": client.browser,
                "os": client.os,
                "is_bot": client.is_bot,
                "cache_status": "HIT" if cache_hit else ("BYPASS" if not endpoint.cacheable else "MISS"),
            }
        )
        self.stats["organic"] += 1
        return event

    # ── Scenario effects ─────────────────────────────────────────────────────
    def _attack_pool(self, scenario: Scenario, profile: AttackerProfile) -> List[Client]:
        pool = self._attack_clients.get(scenario.scenario_id)
        if pool is None:
            zones = HOSTILE_ZONES if profile.hostile_geo else GEO_ZONES
            pool = [
                self._make_client(
                    900_000 + index,
                    zone=self.rng.choice(zones),
                    force_bot=True,
                    ua_pool=profile.ua_pool,
                )
                for index in range(profile.ip_count)
            ]
            self._attack_clients[scenario.scenario_id] = pool
        return pool

    def _attack_path(self, scenario: Scenario, profile: AttackerProfile) -> Tuple[str, str, str]:
        """Return (concrete path, normalised endpoint, query)."""
        if profile.path_mode == "scan":
            path = self.rng.choice(list(profile.paths))
            return path, path, ""
        if profile.path_mode == "paginate":
            cursor = self._scrape_cursor.get(scenario.scenario_id, 0) + 1
            self._scrape_cursor[scenario.scenario_id] = cursor
            template = profile.paths[0]
            return template, template, f"?page={cursor}&limit=200"
        template = self.rng.choice(list(profile.paths))
        return self._materialize_template(template), template, ""

    def _materialize_template(self, template: str) -> str:
        fake = Endpoint(template, "GET", "web-storefront", 1.0, 4.0, 0.5)
        return self._materialize(fake)

    def attack_events(self, ts: float, dt: float) -> List[Dict[str, Any]]:
        if not self.scheduler:
            return []
        events: List[Dict[str, Any]] = []
        for scenario, profile, envelope in self.scheduler.attackers(ts):
            if envelope <= 0.01:
                continue
            pool = self._attack_pool(scenario, profile)
            codes = [s[0] for s in profile.status_profile]
            weights = [s[1] for s in profile.status_profile]
            for client in pool:
                expected = profile.rps_per_ip * envelope * dt
                count = self._poisson(expected)
                session_id = f"s_atk_{client.ip.replace('.', '')}" if profile.reuse_session else None
                for _ in range(count):
                    path, endpoint_template, query = self._attack_path(scenario, profile)
                    status = _weighted(self.rng, codes, weights)
                    latency = self.rng.lognormvariate(profile.lat_mu, profile.lat_sigma)
                    event = self._base_event(ts + self.rng.random() * dt)
                    event.update(
                        {
                            "service": profile.service,
                            "host": self.rng.choice(HOSTS_PER_SERVICE.get(profile.service, ["gw-01"])),
                            "ip": client.ip,
                            "method": profile.method,
                            "path": path,
                            "endpoint": endpoint_template,
                            "query": query,
                            "status": status,
                            "bytes_sent": 0 if status >= 400 else max(80, int(self.rng.lognormvariate(math.log(max(profile.bytes_mean, 200)), 0.4))),
                            "bytes_received": self.rng.randint(80, 900),
                            "response_time_ms": round(latency + 10.0 * client.zone.rtt_factor, 2),
                            "upstream_time_ms": round(latency, 2),
                            "referrer": "-",
                            "user_agent": client.user_agent,
                            "user_id": None,
                            "session_id": session_id or f"s_{_hexid(self.rng, 12)}",
                            "country": client.zone.code,
                            "country_name": client.zone.name,
                            "city": client.zone.city,
                            "lat": client.zone.lat,
                            "lon": client.zone.lon,
                            "asn": client.zone.asn,
                            "org": client.zone.org,
                            "device": "bot",
                            "browser": "unknown",
                            "os": "unknown",
                            "is_bot": True,
                            "cache_status": "BYPASS",
                            "attack_label": scenario.label,
                        }
                    )
                    events.append(event)
                    self.stats["attack"] += 1
        return events

    def apply_mutations(self, event: Dict[str, Any], ts: float) -> Dict[str, Any]:
        if not self.scheduler:
            return event
        for scenario, mutation, envelope in self.scheduler.mutations(ts):
            if envelope <= 0.01:
                continue
            if mutation.target_service and event["service"] != mutation.target_service:
                continue
            if mutation.target_endpoint and event["endpoint"] != mutation.target_endpoint:
                continue
            if self.rng.random() > mutation.affected_fraction * envelope:
                continue

            if mutation.error_statuses:
                codes = [s[0] for s in mutation.error_statuses]
                weights = [s[1] for s in mutation.error_statuses]
                event["status"] = _weighted(self.rng, codes, weights)
                event["bytes_sent"] = self.rng.randint(180, 900)
            if mutation.latency_multiplier != 1.0:
                jitter = 1.0 + self.rng.gauss(0, mutation.latency_jitter) if mutation.latency_jitter else 1.0
                factor = max(0.05, mutation.latency_multiplier * max(jitter, 0.2) * envelope)
                event["response_time_ms"] = round(event["response_time_ms"] * factor, 2)
                event["upstream_time_ms"] = round(event["upstream_time_ms"] * factor, 2)
            event["attack_label"] = scenario.label
            self.stats["mutated"] += 1
        return event

    # ── Sampling ─────────────────────────────────────────────────────────────
    def _poisson(self, lam: float) -> int:
        """Knuth for small λ, normal approximation for large (keeps bursts cheap)."""
        if lam <= 0:
            return 0
        if lam > 30:
            return max(0, int(self.rng.gauss(lam, math.sqrt(lam)) + 0.5))
        target = math.exp(-lam)
        count, product = 0, 1.0
        while True:
            product *= self.rng.random()
            if product <= target:
                return count
            count += 1
            if count > 400:
                return count

    def generate(self, ts: float, dt: float) -> List[Dict[str, Any]]:
        """Produce every event that occurred in ``[ts, ts + dt)``."""
        if self.scheduler:
            self.scheduler.tick(ts)
        rps = self.target_rps(ts)
        count = self._poisson(rps * dt)
        self._prune_sessions(ts)

        events = [self.organic_event(ts + self.rng.random() * dt, rps) for _ in range(count)]
        events = [self.apply_mutations(event, ts) for event in events]
        events.extend(self.attack_events(ts, dt))
        events.sort(key=lambda e: e["timestamp"])
        return events
