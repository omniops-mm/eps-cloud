"""Drives the app's HTTP interface so the metrics have real traffic to show.

Read-heavy weighted mix with exponentially jittered gaps, the arrival pattern
a single real user produces. Writes go against the seeded example data. Runs
for five minutes by default; prints a one-line summary each minute.

    python scripts/traffic.py [--base http://localhost] [--mean 4.0]
"""

import argparse
import datetime
import http.cookiejar
import random
import re
import ssl
import time
import urllib.error
import urllib.request

STREAK_IDS = range(1, 6)  # matches seed.py
TRACKER_IDS = range(1, 5)


def local_today() -> datetime.date:
    """The machine's local date, spelled timezone-aware so the day is explicit."""
    return datetime.datetime.now(datetime.UTC).astimezone().date()


def today() -> str:
    return local_today().isoformat()


def recent_day() -> str:
    return (local_today() - datetime.timedelta(days=random.randint(1, 21))).isoformat()


# (weight, method, path builder)
ACTIONS = [
    (40, "GET", lambda: "/"),
    (15, "GET", lambda: "/journal/"),
    (10, "GET", lambda: f"/journal/{recent_day()}"),
    (10, "GET", lambda: "/calendar/"),
    (5, "GET", lambda: "/settings/"),
    (
        12,
        "POST",
        lambda: (
            f"/journal/{today()}/habit/{random.choice(STREAK_IDS)}/{random.choice(['pass', 'fail'])}"
        ),
    ),
    (6, "POST", lambda: f"/journal/{today()}/tracker/{random.choice(TRACKER_IDS)}"),
    (2, "GET", lambda: f"/missing-{random.randint(1, 999)}"),
]

WEIGHTS = [a[0] for a in ACTIONS]


def hit(opener: urllib.request.OpenerDirector, base: str, method: str, path: str) -> int:
    headers = {}
    if method == "POST":
        with opener.open(base + "/settings/", timeout=10) as page:
            match = re.search(r'name="csrf_token" value="([^"]+)"', page.read().decode())
        if match is None:
            raise ValueError("The settings page did not provide a CSRF token")
        headers["X-CSRFToken"] = match[1]
    request = urllib.request.Request(
        base + path, method=method, headers=headers, data=b"" if method == "POST" else None
    )
    try:
        with opener.open(request, timeout=10) as response:
            return response.status
    except urllib.error.HTTPError as error:
        return error.code


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", default="http://localhost")
    parser.add_argument("--mean", type=float, default=4.0, help="mean seconds between requests")
    parser.add_argument("--duration", type=float, default=300, help="stop after this many seconds")
    parser.add_argument("--ca-file", help="CA certificate for the private HTTPS endpoint")
    args = parser.parse_args()
    if args.mean <= 0 or args.duration <= 0:
        parser.error("mean and duration must be positive")
    opener = urllib.request.build_opener(
        urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()),
        urllib.request.HTTPSHandler(context=ssl.create_default_context(cafile=args.ca_file)),
    )

    counts: dict[int, int] = {}
    window_start = time.monotonic()
    sent = 0
    deadline = window_start + args.duration
    while time.monotonic() < deadline:
        _, method, build = random.choices(ACTIONS, weights=WEIGHTS)[0]
        try:
            status = hit(opener, args.base.rstrip("/"), method, build())
        except Exception:  # noqa: BLE001  # a refused connection is a count, not a crash
            status = 0
        counts[status] = counts.get(status, 0) + 1
        sent += 1
        if time.monotonic() - window_start >= 60:
            print(f"sent={sent} by_status={dict(sorted(counts.items()))}", flush=True)
            window_start = time.monotonic()
        time.sleep(min(random.expovariate(1.0 / args.mean), max(0, deadline - time.monotonic())))
    print(f"sent={sent} by_status={dict(sorted(counts.items()))}", flush=True)


if __name__ == "__main__":
    main()
