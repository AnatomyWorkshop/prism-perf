"""Traffic forecasting for software designers.

Given historical traffic data (CSV or JSON), forecasts future peak load
and feeds it back into the topology check as the throughput target.

This closes the loop:
  historical data → forecast peak QPS → topology check → verdict

No ML framework required — uses simple but robust methods:
  - Seasonal decomposition (weekly pattern)
  - Trend extrapolation (linear regression)
  - Peak multiplier (p99 of daily peaks)
"""
import json
import math
from typing import Optional


def forecast_peak_qps(data_path: str, horizon_days: int = 30) -> Optional[dict]:
    """
    Forecast peak QPS for the next horizon_days.

    Input formats supported:
      CSV: timestamp,qps  (one row per minute/hour)
      JSON: [{"ts": "2024-01-01T00:00:00", "qps": 1234}, ...]
      JSON: {"data": [{"timestamp": ..., "value": ...}]}  (Datadog/Prometheus style)

    Returns:
      {
        "forecast_p99_qps": float,   # use this as throughput target
        "forecast_peak_qps": float,  # absolute max expected
        "trend": "growing|stable|declining",
        "growth_rate_pct": float,    # per month
        "confidence": "high|medium|low",
        "method": str,
        "data_points": int,
      }
    """
    try:
        series = _load_series(data_path)
    except Exception as e:
        return {"error": f"Could not load data: {e}"}

    if len(series) < 10:
        return {"error": f"Too few data points ({len(series)}), need at least 10"}

    values = [v for _, v in series]

    # Basic stats
    n = len(values)
    mean_qps = sum(values) / n
    sorted_vals = sorted(values)
    p50 = sorted_vals[n // 2]
    p95_idx = min(int(n * 0.95), n - 1)
    p99_idx = min(int(n * 0.99), n - 1)
    p95 = sorted_vals[p95_idx]
    p99 = sorted_vals[p99_idx]
    peak = sorted_vals[-1]

    # Trend: linear regression on values
    x_mean = (n - 1) / 2
    y_mean = mean_qps
    numerator = sum((i - x_mean) * (v - y_mean) for i, v in enumerate(values))
    denominator = sum((i - x_mean) ** 2 for i in range(n))
    slope = numerator / denominator if denominator > 0 else 0

    # Slope per data point → per month
    # Estimate data frequency from timestamps
    if len(series) >= 2:
        ts_diff = series[-1][0] - series[0][0]
        points_per_day = n / max(ts_diff / 86400, 1)
    else:
        points_per_day = 24  # assume hourly

    slope_per_day = slope * points_per_day
    slope_per_month = slope_per_day * 30
    growth_rate_pct = (slope_per_month / mean_qps * 100) if mean_qps > 0 else 0

    if growth_rate_pct > 5:
        trend = "growing"
    elif growth_rate_pct < -5:
        trend = "declining"
    else:
        trend = "stable"

    # Forecast: project trend forward + apply peak multiplier
    forecast_mean = mean_qps + slope_per_day * horizon_days
    peak_multiplier = peak / mean_qps if mean_qps > 0 else 2.0
    forecast_peak = forecast_mean * peak_multiplier
    forecast_p99 = forecast_mean * (p99 / mean_qps) if mean_qps > 0 else p99

    # Confidence based on data volume and variance
    cv = (sum((v - mean_qps) ** 2 for v in values) / n) ** 0.5 / mean_qps if mean_qps > 0 else 1
    if n >= 168 and cv < 0.5:  # 1 week of hourly data, low variance
        confidence = "high"
    elif n >= 48:
        confidence = "medium"
    else:
        confidence = "low"

    return {
        "forecast_p99_qps": round(forecast_p99, 1),
        "forecast_peak_qps": round(forecast_peak, 1),
        "current_mean_qps": round(mean_qps, 1),
        "current_p99_qps": round(p99, 1),
        "current_peak_qps": round(peak, 1),
        "trend": trend,
        "growth_rate_pct": round(growth_rate_pct, 1),
        "horizon_days": horizon_days,
        "confidence": confidence,
        "method": "linear_trend + peak_multiplier",
        "data_points": n,
    }


def _load_series(path: str) -> list[tuple[float, float]]:
    """Load time series as list of (timestamp_seconds, qps) tuples."""
    with open(path, "r", encoding="utf-8") as f:
        content = f.read().strip()

    # Try JSON
    if content.startswith("[") or content.startswith("{"):
        data = json.loads(content)

        # Prometheus/Datadog style: {"data": [{"timestamp": ..., "value": ...}]}
        if isinstance(data, dict) and "data" in data:
            data = data["data"]

        if isinstance(data, list) and data:
            series = []
            for item in data:
                if isinstance(item, dict):
                    ts = _parse_ts(item.get("ts") or item.get("timestamp") or item.get("time", 0))
                    val = float(item.get("qps") or item.get("value") or item.get("rps") or 0)
                    series.append((ts, val))
                elif isinstance(item, (list, tuple)) and len(item) >= 2:
                    series.append((float(item[0]), float(item[1])))
            return series

    # Try CSV
    lines = content.split("\n")
    series = []
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split(",")
        if len(parts) >= 2:
            try:
                ts = _parse_ts(parts[0].strip())
                val = float(parts[1].strip())
                series.append((ts, val))
            except (ValueError, TypeError):
                continue  # skip header or malformed lines

    return series


def _parse_ts(ts) -> float:
    """Parse timestamp to Unix seconds."""
    if isinstance(ts, (int, float)):
        return float(ts)
    if isinstance(ts, str):
        # ISO format
        try:
            import datetime
            dt = datetime.datetime.fromisoformat(ts.replace("Z", "+00:00"))
            return dt.timestamp()
        except Exception:
            pass
        # Try as float string
        try:
            return float(ts)
        except ValueError:
            pass
    return 0.0


def format_forecast(forecast: dict) -> str:
    """Format forecast for terminal output."""
    if not forecast or "error" in forecast:
        err = forecast.get("error", "unknown") if forecast else "no data"
        return f"\n  [Traffic forecast unavailable: {err}]"

    lines = ["", "=" * 60, "TRAFFIC FORECAST", "=" * 60, ""]

    lines.append(f"  Horizon:          {forecast['horizon_days']} days")
    lines.append(f"  Data points:      {forecast['data_points']}")
    lines.append(f"  Confidence:       {forecast['confidence']}")
    lines.append("")
    lines.append(f"  Current mean QPS: {forecast['current_mean_qps']}")
    lines.append(f"  Current p99 QPS:  {forecast['current_p99_qps']}")
    lines.append(f"  Current peak QPS: {forecast['current_peak_qps']}")
    lines.append("")

    trend = forecast["trend"]
    rate = forecast["growth_rate_pct"]
    trend_str = f"{trend} ({rate:+.1f}%/month)"
    lines.append(f"  Trend:            {trend_str}")
    lines.append("")
    lines.append(f"  Forecast p99 QPS: {forecast['forecast_p99_qps']}  ← use as throughput target")
    lines.append(f"  Forecast peak:    {forecast['forecast_peak_qps']}")
    lines.append("")

    if forecast["trend"] == "growing" and forecast["growth_rate_pct"] > 20:
        lines.append("  WARNING: High growth rate. Re-check topology feasibility monthly.")
        lines.append("")

    return "\n".join(lines)


def generate_sample_traffic_data(output_path: str, base_qps: float = 500,
                                  days: int = 14, growth_pct: float = 10):
    """Generate sample traffic CSV for testing."""
    import datetime
    import random

    random.seed(42)
    start = datetime.datetime(2024, 1, 1, 0, 0, 0)
    rows = ["timestamp,qps"]

    for hour in range(days * 24):
        dt = start + datetime.timedelta(hours=hour)
        ts = dt.isoformat()

        # Daily pattern: peak at 10am and 8pm
        hour_of_day = dt.hour
        daily_factor = 0.3 + 0.7 * math.exp(-((hour_of_day - 10) ** 2) / 18)
        daily_factor = max(daily_factor,
                           0.3 + 0.6 * math.exp(-((hour_of_day - 20) ** 2) / 8))

        # Weekly pattern: weekdays 1.3x, weekends 0.7x
        weekly_factor = 1.3 if dt.weekday() < 5 else 0.7

        # Growth trend
        growth_factor = 1 + (growth_pct / 100) * (hour / (days * 24))

        # Noise
        noise = random.gauss(1.0, 0.1)

        qps = base_qps * daily_factor * weekly_factor * growth_factor * noise
        rows.append(f"{ts},{qps:.1f}")

    with open(output_path, "w", encoding="utf-8") as f:
        f.write("\n".join(rows))
