"""
Weather tool — current weather + forecast via Open-Meteo (no API key).
Geocodes a city name, then fetches conditions.
"""

from __future__ import annotations

import httpx

SCHEMA = {
    "type": "function",
    "function": {
        "name": "weather",
        "description": (
            "Get current weather and a short forecast for a city. Use for any question "
            "about weather, temperature, rain, or forecast in a location."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "city": {"type": "string", "description": "City name, e.g. 'Santiago', 'Madrid', 'Tokyo'."},
            },
            "required": ["city"],
        },
    },
}

_HEADERS = {"User-Agent": "cortex/0.1"}

# WMO weather codes → English description
_WMO = {
    0: "clear sky", 1: "mainly clear", 2: "partly cloudy", 3: "overcast",
    45: "fog", 48: "freezing fog", 51: "light drizzle", 53: "drizzle",
    55: "heavy drizzle", 61: "light rain", 63: "rain", 65: "heavy rain",
    71: "light snow", 73: "snow", 75: "heavy snow", 80: "light showers",
    81: "showers", 82: "violent showers", 95: "thunderstorm", 96: "thunderstorm with hail",
}


def execute(city: str) -> str:
    # 1. Geocode
    try:
        with httpx.Client(timeout=10.0, headers=_HEADERS) as c:
            g = c.get("https://geocoding-api.open-meteo.com/v1/search",
                      params={"name": city, "count": 1, "language": "es"})
            g.raise_for_status()
            results = g.json().get("results")
    except Exception as e:
        return f"[ERROR] Geocoding failed: {e}"

    if not results:
        return f"[ERROR] City '{city}' not found."

    loc = results[0]
    lat, lon = loc["latitude"], loc["longitude"]
    name = loc["name"]
    country = loc.get("country", "")

    # 2. Weather
    try:
        with httpx.Client(timeout=10.0, headers=_HEADERS) as c:
            w = c.get("https://api.open-meteo.com/v1/forecast", params={
                "latitude": lat, "longitude": lon,
                "current": "temperature_2m,relative_humidity_2m,weather_code,wind_speed_10m",
                "daily": "weather_code,temperature_2m_max,temperature_2m_min",
                "timezone": "auto", "forecast_days": 3,
            })
            w.raise_for_status()
            data = w.json()
    except Exception as e:
        return f"[ERROR] Weather request failed: {e}"

    cur = data.get("current", {})
    code = cur.get("weather_code", -1)
    desc = _WMO.get(code, "desconocido")

    lines = [
        f"**Weather in {name}, {country}**",
        f"- Now: {cur.get('temperature_2m')}°C, {desc}",
        f"- Humidity: {cur.get('relative_humidity_2m')}%  ·  Wind: {cur.get('wind_speed_10m')} km/h",
    ]

    daily = data.get("daily", {})
    dates = daily.get("time", [])
    if dates:
        lines.append("- Forecast:")
        for i, d in enumerate(dates[:3]):
            dcode = _WMO.get(daily["weather_code"][i], "?")
            tmax = daily["temperature_2m_max"][i]
            tmin = daily["temperature_2m_min"][i]
            lines.append(f"    {d}: {tmin}–{tmax}°C, {dcode}")

    return "\n".join(lines)
