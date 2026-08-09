"""Drives the app's HTTP interface so the metrics have real traffic to show.

Read-heavy weighted mix with exponentially jittered gaps, the arrival pattern
a single real user produces. Writes go against the seeded example data. Runs
until stopped; prints a one-line summary each minute.

    python scripts/traffic.py [--base http://localhost] [--mean 4.0]
"""

import argparse
import datetime
import random
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


def hit(base: str, method: str, path: str) -> int:
    request = urllib.request.Request(
        base + path, method=method, data=b"" if method == "POST" else None
    )
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            return response.status
    except urllib.error.HTTPError as error:
        return error.code


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", default="http://localhost")
    parser.add_argument("--mean", type=float, default=4.0, help="mean seconds between requests")
    args = parser.parse_args()

    counts: dict[int, int] = {}
    window_start = time.monotonic()
    sent = 0
    while True:
        _, method, build = random.choices(ACTIONS, weights=WEIGHTS)[0]
        try:
            status = hit(args.base, method, build())
        except Exception:  # noqa: BLE001  # a refused connection is a count, not a crash
            status = 0
        counts[status] = counts.get(status, 0) + 1
        sent += 1
        if time.monotonic() - window_start >= 60:
            print(f"sent={sent} by_status={dict(sorted(counts.items()))}", flush=True)
            window_start = time.monotonic()
        time.sleep(random.expovariate(1.0 / args.mean))


if __name__ == "__main__":
    main()
