"""Static world model for the synthetic traffic: endpoints, geography, clients.

Every number here (weights, latency distributions, error rates) was chosen so
the aggregate stream looks like a real mid-size e-commerce platform rather than
uniform noise — that is what makes the downstream statistics meaningful.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple


@dataclass(frozen=True)
class Endpoint:
    """One route in the catalog.

    ``lat_mu``/``lat_sigma`` parameterise a log-normal response-time
    distribution (in log-space of milliseconds), which is the standard shape for
    web latency: a tight body with a long right tail.
    """

    path: str
    method: str
    service: str
    weight: float
    lat_mu: float
    lat_sigma: float
    err_4xx: float = 0.004
    err_5xx: float = 0.002
    bytes_mean: int = 12_000
    cacheable: bool = False
    auth_required: bool = False
    dynamic: bool = False  # path contains an id segment
    group: str = "browse"
    capacity_rps: float = 400.0  # queueing knee: latency inflates past this


ENDPOINTS: List[Endpoint] = [
    Endpoint("/", "GET", "web-storefront", 9.0, 4.10, 0.45, 0.002, 0.001, 32_000, True, group="entry"),
    Endpoint("/category/{slug}", "GET", "web-storefront", 7.0, 4.35, 0.50, 0.006, 0.002, 41_000, True, dynamic=True, group="browse"),
    Endpoint("/products/{id}", "GET", "web-storefront", 11.0, 4.45, 0.55, 0.010, 0.002, 38_000, True, dynamic=True, group="browse"),
    Endpoint("/blog/{slug}", "GET", "web-storefront", 2.0, 4.20, 0.40, 0.008, 0.001, 26_000, True, dynamic=True, group="browse"),
    Endpoint("/api/v1/products", "GET", "api-gateway", 10.0, 4.05, 0.55, 0.004, 0.003, 18_000, True, group="api"),
    Endpoint("/api/v1/products/{id}", "GET", "api-gateway", 8.0, 3.95, 0.50, 0.012, 0.003, 9_500, True, dynamic=True, group="api"),
    Endpoint("/api/v1/search", "GET", "search-svc", 8.5, 5.05, 0.65, 0.007, 0.006, 22_000, group="api", capacity_rps=180),
    Endpoint("/api/v1/recommendations", "GET", "search-svc", 4.0, 5.25, 0.70, 0.003, 0.008, 16_000, group="api", capacity_rps=120),
    Endpoint("/api/v1/inventory", "GET", "api-gateway", 3.0, 4.30, 0.45, 0.004, 0.004, 6_400, group="api"),
    Endpoint("/api/v1/cart", "GET", "checkout-svc", 4.5, 4.15, 0.45, 0.020, 0.003, 5_200, auth_required=True, group="cart"),
    Endpoint("/api/v1/cart", "POST", "checkout-svc", 4.0, 4.60, 0.55, 0.025, 0.005, 2_100, auth_required=True, group="cart"),
    Endpoint("/api/v1/checkout", "POST", "checkout-svc", 2.2, 5.60, 0.60, 0.030, 0.010, 3_400, auth_required=True, group="checkout", capacity_rps=90),
    Endpoint("/api/v1/payments", "POST", "checkout-svc", 1.8, 6.05, 0.55, 0.035, 0.014, 2_800, auth_required=True, group="checkout", capacity_rps=70),
    Endpoint("/api/v1/auth/login", "POST", "api-gateway", 3.2, 5.10, 0.50, 0.070, 0.004, 1_900, group="auth"),
    Endpoint("/api/v1/auth/refresh", "POST", "api-gateway", 2.4, 4.20, 0.40, 0.015, 0.002, 1_200, group="auth"),
    Endpoint("/api/v1/auth/register", "POST", "api-gateway", 0.8, 5.35, 0.55, 0.045, 0.006, 2_000, group="auth"),
    Endpoint("/api/v1/user/profile", "GET", "api-gateway", 3.0, 4.10, 0.45, 0.018, 0.003, 4_800, auth_required=True, group="account"),
    Endpoint("/api/v1/orders", "GET", "checkout-svc", 2.6, 4.85, 0.55, 0.016, 0.005, 11_000, auth_required=True, group="account"),
    Endpoint("/api/v1/orders/{id}", "GET", "checkout-svc", 1.9, 4.75, 0.50, 0.022, 0.005, 7_300, auth_required=True, dynamic=True, group="account"),
    Endpoint("/api/v1/reviews", "POST", "api-gateway", 1.1, 4.90, 0.55, 0.028, 0.006, 1_500, auth_required=True, group="engage"),
    Endpoint("/static/app.{hash}.js", "GET", "media-cdn", 9.0, 2.90, 0.40, 0.001, 0.0005, 148_000, True, dynamic=True, group="asset"),
    Endpoint("/static/main.{hash}.css", "GET", "media-cdn", 6.0, 2.70, 0.35, 0.001, 0.0005, 62_000, True, dynamic=True, group="asset"),
    Endpoint("/media/{id}.jpg", "GET", "media-cdn", 12.0, 3.10, 0.50, 0.005, 0.001, 210_000, True, dynamic=True, group="asset"),
    Endpoint("/health", "GET", "api-gateway", 2.0, 1.60, 0.25, 0.0002, 0.0005, 280, group="ops"),
    Endpoint("/metrics", "GET", "api-gateway", 0.6, 2.40, 0.30, 0.0002, 0.0005, 18_000, group="ops"),
]

#: Session journey model.  Keys are endpoint groups; values are the transition
#: probabilities to the next group.  ``__end__`` terminates the session.
JOURNEY: Dict[str, Dict[str, float]] = {
    "__start__": {"entry": 0.42, "browse": 0.28, "api": 0.16, "auth": 0.08, "asset": 0.06},
    "entry": {"browse": 0.46, "asset": 0.24, "api": 0.14, "auth": 0.05, "__end__": 0.11},
    "browse": {"browse": 0.31, "api": 0.24, "asset": 0.18, "cart": 0.12, "__end__": 0.15},
    "api": {"api": 0.34, "browse": 0.22, "cart": 0.14, "asset": 0.10, "account": 0.06, "__end__": 0.14},
    "asset": {"browse": 0.38, "api": 0.22, "asset": 0.16, "__end__": 0.24},
    "auth": {"account": 0.34, "browse": 0.26, "api": 0.20, "cart": 0.08, "__end__": 0.12},
    "cart": {"cart": 0.24, "checkout": 0.26, "browse": 0.22, "api": 0.12, "__end__": 0.16},
    "checkout": {"checkout": 0.22, "account": 0.24, "browse": 0.16, "__end__": 0.38},
    "account": {"account": 0.26, "browse": 0.24, "api": 0.18, "__end__": 0.32},
    "engage": {"browse": 0.40, "__end__": 0.60},
    "ops": {"__end__": 1.0},
}


@dataclass(frozen=True)
class GeoZone:
    code: str
    name: str
    city: str
    lat: float
    lon: float
    weight: float
    prefixes: Tuple[str, ...]
    asn: str
    org: str
    #: Multiplier on network round-trip, i.e. distance from the origin region.
    rtt_factor: float = 1.0


GEO_ZONES: List[GeoZone] = [
    GeoZone("US", "United States", "Ashburn", 39.04, -77.49, 30.0, ("52.94", "34.201", "3.88"), "AS14618", "Amazon-AES", 1.0),
    GeoZone("US", "United States", "San Francisco", 37.77, -122.42, 12.0, ("104.16", "172.67"), "AS13335", "Cloudflare", 1.05),
    GeoZone("DE", "Germany", "Frankfurt", 50.11, 8.68, 8.5, ("18.184", "35.157"), "AS16509", "Amazon.com", 1.35),
    GeoZone("GB", "United Kingdom", "London", 51.51, -0.13, 7.5, ("35.176", "18.130"), "AS16509", "Amazon.com", 1.30),
    GeoZone("IN", "India", "Mumbai", 19.08, 72.88, 9.0, ("13.234", "65.0"), "AS16509", "Amazon.com", 1.75),
    GeoZone("BR", "Brazil", "Sao Paulo", -23.55, -46.63, 5.0, ("18.228", "177.71"), "AS16509", "Amazon.com", 1.80),
    GeoZone("JP", "Japan", "Tokyo", 35.68, 139.69, 5.5, ("13.112", "18.176"), "AS16509", "Amazon.com", 1.60),
    GeoZone("SG", "Singapore", "Singapore", 1.35, 103.82, 4.0, ("13.228", "18.140"), "AS16509", "Amazon.com", 1.70),
    GeoZone("FR", "France", "Paris", 48.86, 2.35, 4.5, ("15.188", "35.180"), "AS16509", "Amazon.com", 1.32),
    GeoZone("CA", "Canada", "Toronto", 43.65, -79.38, 3.5, ("35.182", "99.79"), "AS16509", "Amazon.com", 1.12),
    GeoZone("AU", "Australia", "Sydney", -33.87, 151.21, 3.0, ("13.54", "54.153"), "AS16509", "Amazon.com", 1.85),
    GeoZone("NL", "Netherlands", "Amsterdam", 52.37, 4.90, 2.5, ("52.16", "108.128"), "AS16509", "Amazon.com", 1.30),
    GeoZone("ES", "Spain", "Madrid", 40.42, -3.70, 2.0, ("18.100", "35.181"), "AS16509", "Amazon.com", 1.38),
    GeoZone("ZA", "South Africa", "Cape Town", -33.92, 18.42, 1.4, ("13.244", "102.133"), "AS16509", "Amazon.com", 1.95),
    GeoZone("MX", "Mexico", "Queretaro", 20.59, -100.39, 1.6, ("18.204", "54.233"), "AS16509", "Amazon.com", 1.40),
]

#: Origins used by attack scenarios — deliberately different from the organic mix
#: so geographic detectors have something meaningful to find.
HOSTILE_ZONES: List[GeoZone] = [
    GeoZone("RU", "Russia", "Moscow", 55.75, 37.62, 1.0, ("45.9", "185.220", "91.219"), "AS49505", "Selectel", 2.1),
    GeoZone("CN", "China", "Shanghai", 31.23, 121.47, 1.0, ("120.24", "47.100"), "AS37963", "Alibaba", 2.0),
    GeoZone("NL", "Netherlands", "Dronten", 52.53, 5.72, 1.0, ("45.83", "194.26"), "AS60781", "LeaseWeb", 1.4),
    GeoZone("VN", "Vietnam", "Hanoi", 21.03, 105.85, 1.0, ("113.160", "14.161"), "AS45899", "VNPT", 2.2),
    GeoZone("BG", "Bulgaria", "Sofia", 42.70, 23.32, 1.0, ("5.101", "217.12"), "AS203380", "Bulgarian Hosting", 1.6),
]

BROWSERS: List[Tuple[str, str, str, str, float]] = [
    # (browser, os, device, user-agent, weight)
    ("Chrome", "Windows", "desktop",
     "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36", 28.0),
    ("Chrome", "macOS", "desktop",
     "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36", 12.0),
    ("Safari", "iOS", "mobile",
     "Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 Mobile/15E148 Safari/604.1", 20.0),
    ("Chrome", "Android", "mobile",
     "Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Mobile Safari/537.36", 16.0),
    ("Safari", "macOS", "desktop",
     "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 Safari/605.1.15", 7.0),
    ("Firefox", "Windows", "desktop",
     "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:127.0) Gecko/20100101 Firefox/127.0", 6.0),
    ("Edge", "Windows", "desktop",
     "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36 Edg/126.0.0.0", 6.0),
    ("Chrome", "Linux", "desktop",
     "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36", 3.0),
    ("Samsung Internet", "Android", "mobile",
     "Mozilla/5.0 (Linux; Android 14; SM-S918B) AppleWebKit/537.36 (KHTML, like Gecko) SamsungBrowser/25.0 Chrome/121.0.0.0 Mobile Safari/537.36", 2.0),
]

BOTS: List[Tuple[str, str, float]] = [
    ("Googlebot", "Mozilla/5.0 (compatible; Googlebot/2.1; +http://www.google.com/bot.html)", 30.0),
    ("bingbot", "Mozilla/5.0 (compatible; bingbot/2.0; +http://www.bing.com/bingbot.htm)", 18.0),
    ("AhrefsBot", "Mozilla/5.0 (compatible; AhrefsBot/7.0; +http://ahrefs.com/robot/)", 12.0),
    ("SemrushBot", "Mozilla/5.0 (compatible; SemrushBot/7~bl; +http://www.semrush.com/bot.html)", 8.0),
    ("UptimeRobot", "Mozilla/5.0+(compatible; UptimeRobot/2.0; http://www.uptimerobot.com/)", 14.0),
    ("python-requests", "python-requests/2.32.3", 10.0),
    ("curl", "curl/8.7.1", 8.0),
]

ATTACK_TOOLS: List[str] = [
    "Mozilla/5.0 (compatible; Nmap Scripting Engine; https://nmap.org/book/nse.html)",
    "sqlmap/1.8.5#stable (https://sqlmap.org)",
    "Mozilla/5.0 (X11; Linux x86_64) Gecko/20100101 Firefox/91.0",
    "python-urllib3/2.2.1",
    "Go-http-client/2.0",
    "masscan/1.3",
    "WPScan v3.8.25 (https://wpscan.com/wordpress-security-scanner)",
]

REFERRERS: List[Tuple[str, float]] = [
    ("-", 34.0),
    ("https://www.google.com/", 26.0),
    ("https://www.bing.com/", 5.0),
    ("https://t.co/", 6.0),
    ("https://www.reddit.com/", 5.0),
    ("https://news.ycombinator.com/", 3.0),
    ("https://www.instagram.com/", 7.0),
    ("https://mail.google.com/", 4.0),
    ("https://www.facebook.com/", 10.0),
]

#: Paths probed during a scanning scenario — the shape real scanners produce.
SCAN_PATHS: List[str] = [
    "/wp-admin/", "/wp-login.php", "/.env", "/.git/config", "/admin", "/admin/login",
    "/phpmyadmin/", "/config.json", "/backup.sql", "/api/v1/../../etc/passwd",
    "/actuator/env", "/actuator/health", "/server-status", "/.aws/credentials",
    "/vendor/phpunit/phpunit/src/Util/PHP/eval-stdin.php", "/cgi-bin/test.cgi",
    "/api/swagger.json", "/graphql", "/debug/pprof/", "/console", "/jenkins/login",
    "/solr/admin/cores", "/xmlrpc.php", "/telescope/requests", "/_ignition/execute-solution",
    "/api/v2/internal/users", "/private/keys", "/dump.sql", "/db_backup.tar.gz",
    "/api/v1/admin/users", "/login.action", "/struts2-showcase/", "/owa/auth/logon.aspx",
]

CATEGORY_SLUGS = [
    "electronics", "home-kitchen", "outdoor", "fashion", "beauty", "grocery",
    "toys", "sports", "automotive", "books", "office", "pet-supplies",
]

BLOG_SLUGS = [
    "spring-lookbook", "how-we-scaled-checkout", "sustainable-packaging",
    "black-friday-guide", "engineering-culture", "supply-chain-2026",
]

REGIONS = ["us-east-1", "us-west-2", "eu-west-1", "eu-central-1", "ap-south-1", "ap-southeast-1"]

HOSTS_PER_SERVICE: Dict[str, List[str]] = {
    "web-storefront": ["web-01", "web-02", "web-03", "web-04"],
    "api-gateway": ["gw-01", "gw-02", "gw-03"],
    "checkout-svc": ["checkout-01", "checkout-02"],
    "search-svc": ["search-01", "search-02"],
    "media-cdn": ["edge-lhr", "edge-iad", "edge-fra", "edge-sin"],
}

STATUS_BY_INTENT: Dict[str, List[Tuple[int, float]]] = {
    "success": [(200, 88.0), (201, 4.0), (204, 3.0), (206, 2.0), (301, 1.0), (302, 1.0), (304, 1.0)],
    "client_error": [(400, 22.0), (401, 24.0), (403, 16.0), (404, 26.0), (409, 5.0), (422, 4.0), (429, 3.0)],
    "server_error": [(500, 52.0), (502, 20.0), (503, 20.0), (504, 8.0)],
}


def endpoint_key(endpoint: Endpoint) -> str:
    return f"{endpoint.method} {endpoint.path}"


ENDPOINTS_BY_GROUP: Dict[str, List[Endpoint]] = {}
for _endpoint in ENDPOINTS:
    ENDPOINTS_BY_GROUP.setdefault(_endpoint.group, []).append(_endpoint)
