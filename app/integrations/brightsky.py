"""Weather via BrightSky (https://brightsky.dev): free, no API key.

One fetch per day, cached in weather_cache. The dashboard asks for a summary;
if the cache has today's row it is used, otherwise one request is attempted
with a short timeout. Any failure means "no weather today", never an error
page: the widget is decoration, not a dependency.
"""

import datetime
import decimal
import json
import urllib.parse
import urllib.request

from sqlalchemy.orm import Session

from app.models import UserSettings, WeatherCache

API_URL = "https://api.brightsky.dev/weather"
FETCH_TIMEOUT_SECONDS = 3


def fetch_forecast(lat: decimal.Decimal, lon: decimal.Decimal, day: datetime.date) -> dict:
    """One day of hourly forecast, raw from the API. Raises on any failure."""
    query = urllib.parse.urlencode({"lat": str(lat), "lon": str(lon), "date": day.isoformat()})
    with urllib.request.urlopen(  # fixed https host, query is ours
        f"{API_URL}?{query}", timeout=FETCH_TIMEOUT_SECONDS
    ) as response:
        return json.load(response)


def summarize(raw: dict) -> dict | None:
    """Reduce hourly records to what the widget shows: temp range, rain chance."""
    records = [r for r in raw.get("weather", []) if r.get("temperature") is not None]
    if not records:
        return None
    temps = [r["temperature"] for r in records]
    rain = max((r.get("precipitation_probability") or 0) for r in records)
    return {"temp_min": round(min(temps)), "temp_max": round(max(temps)), "rain_chance": rain}


def weather_summary(session: Session, day: datetime.date) -> dict | None:
    """Today's summary from cache, fetching once if the cache is empty."""
    cached = session.get(WeatherCache, day)
    if cached is None:
        settings = session.get(UserSettings, 1)
        if settings is None:
            return None
        try:
            raw = fetch_forecast(settings.weather_location_lat, settings.weather_location_lon, day)
        except Exception:  # noqa: BLE001  # any network failure means "no widget"
            return None
        cached = WeatherCache(
            date=day,
            raw_payload=raw,
            fetched_at=datetime.datetime.now(datetime.UTC),
        )
        session.add(cached)
        session.commit()
    return summarize(cached.raw_payload)
