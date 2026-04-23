"""MCP tools for querying sleep records."""

import logging
from typing import Any

from fastmcp import FastMCP

from app.services.api_client import client
from app.utils import normalize_datetime

logger = logging.getLogger(__name__)

# Create router for sleep-related tools
sleep_router = FastMCP(name="Sleep Tools")


def _format_minutes(minutes: int | float | None) -> str | None:
    """Format minutes into a human-readable hours/minutes string."""
    if minutes is None:
        return None
    total_minutes = int(round(minutes))
    hours, mins = divmod(total_minutes, 60)
    if hours and mins:
        return f"{hours}h {mins}m"
    if hours:
        return f"{hours}h"
    return f"{mins}m"


def _build_user_context(user_data: dict[str, Any]) -> dict[str, str | None]:
    """Map user payload into a compact MCP response shape."""
    return {
        "id": str(user_data.get("id")),
        "first_name": user_data.get("first_name"),
        "last_name": user_data.get("last_name"),
    }


def _normalize_sleep_record(record: dict[str, Any]) -> dict[str, Any]:
    """Normalize a sleep summary record while preserving all API fields."""
    source_raw = record.get("source")
    source: dict[str, Any] | None = None
    if isinstance(source_raw, dict):
        source = {
            "provider": source_raw.get("provider"),
            "device": source_raw.get("device"),
        }
    elif source_raw is not None:
        source = {
            "provider": source_raw,
            "device": None,
        }

    stages_raw = record.get("stages")
    stages: dict[str, Any] | None = None
    if isinstance(stages_raw, dict):
        stages = {
            "awake_minutes": stages_raw.get("awake_minutes"),
            "light_minutes": stages_raw.get("light_minutes"),
            "deep_minutes": stages_raw.get("deep_minutes"),
            "rem_minutes": stages_raw.get("rem_minutes"),
        }

    duration_minutes = record.get("duration_minutes")
    time_in_bed_minutes = record.get("time_in_bed_minutes")

    return {
        "date": str(record.get("date")) if record.get("date") is not None else None,
        "source": source,
        "start_time": normalize_datetime(record.get("start_time")),
        "end_time": normalize_datetime(record.get("end_time")),
        "duration_minutes": duration_minutes,
        "duration_formatted": _format_minutes(duration_minutes),
        "time_in_bed_minutes": time_in_bed_minutes,
        "time_in_bed_formatted": _format_minutes(time_in_bed_minutes),
        "efficiency_percent": record.get("efficiency_percent"),
        "stages": stages,
        "interruptions_count": record.get("interruptions_count"),
        "nap_count": record.get("nap_count"),
        "nap_duration_minutes": record.get("nap_duration_minutes"),
        "avg_heart_rate_bpm": record.get("avg_heart_rate_bpm"),
        "avg_hrv_sdnn_ms": record.get("avg_hrv_sdnn_ms"),
        "avg_respiratory_rate": record.get("avg_respiratory_rate"),
        "avg_spo2_percent": record.get("avg_spo2_percent"),
    }


def _to_float(value: Any) -> float | None:
    """Safely convert values to float."""
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _build_sleep_highlight(record: dict[str, Any]) -> dict[str, Any]:
    """Build a compact record snippet used in analysis highlights."""
    source = record.get("source") or {}
    return {
        "date": record.get("date"),
        "start_time": record.get("start_time"),
        "end_time": record.get("end_time"),
        "duration_minutes": record.get("duration_minutes"),
        "duration_formatted": record.get("duration_formatted"),
        "efficiency_percent": record.get("efficiency_percent"),
        "source_provider": source.get("provider"),
        "source_device": source.get("device"),
    }


def _compute_advanced_sleep_analysis(records: list[dict[str, Any]]) -> dict[str, Any]:
    """Compute analysis-friendly aggregates from normalized sleep records."""
    durations: list[float] = []
    time_in_bed_values: list[float] = []
    efficiencies: list[float] = []
    avg_sleep_hr_values: list[float] = []
    nap_count_values: list[int] = []
    nap_duration_values: list[float] = []
    stage_totals = {
        "awake_minutes": 0,
        "light_minutes": 0,
        "deep_minutes": 0,
        "rem_minutes": 0,
    }
    has_stage_values = False

    records_with_duration: list[dict[str, Any]] = []

    for record in records:
        duration = _to_float(record.get("duration_minutes"))
        if duration is not None:
            durations.append(duration)
            records_with_duration.append(record)

        time_in_bed = _to_float(record.get("time_in_bed_minutes"))
        if time_in_bed is not None:
            time_in_bed_values.append(time_in_bed)

        efficiency = _to_float(record.get("efficiency_percent"))
        if efficiency is not None:
            efficiencies.append(efficiency)

        avg_sleep_hr = _to_float(record.get("avg_heart_rate_bpm"))
        if avg_sleep_hr is not None:
            avg_sleep_hr_values.append(avg_sleep_hr)

        nap_count = record.get("nap_count")
        if isinstance(nap_count, int):
            nap_count_values.append(nap_count)

        nap_duration = _to_float(record.get("nap_duration_minutes"))
        if nap_duration is not None:
            nap_duration_values.append(nap_duration)

        stages = record.get("stages")
        if isinstance(stages, dict):
            for key in stage_totals:
                value = _to_float(stages.get(key))
                if value is not None:
                    stage_totals[key] += int(round(value))
                    has_stage_values = True

    longest_sleep_night = None
    shortest_sleep_night = None
    if records_with_duration:
        longest = max(records_with_duration, key=lambda item: _to_float(item.get("duration_minutes")) or 0)
        shortest = min(records_with_duration, key=lambda item: _to_float(item.get("duration_minutes")) or 0)
        longest_sleep_night = _build_sleep_highlight(longest)
        shortest_sleep_night = _build_sleep_highlight(shortest)

    duration_avg = sum(durations) / len(durations) if durations else None
    duration_min = min(durations) if durations else None
    duration_max = max(durations) if durations else None

    time_in_bed_avg = sum(time_in_bed_values) / len(time_in_bed_values) if time_in_bed_values else None

    return {
        "total_nights": len(records),
        "nights_with_main_sleep_data": len(durations),
        "nights_with_stage_breakdown": (
            len([record for record in records if isinstance(record.get("stages"), dict)]) if records else 0
        ),
        "duration": {
            "avg_minutes": round(duration_avg) if duration_avg is not None else None,
            "avg_formatted": _format_minutes(duration_avg),
            "min_minutes": int(round(duration_min)) if duration_min is not None else None,
            "max_minutes": int(round(duration_max)) if duration_max is not None else None,
        },
        "time_in_bed": {
            "avg_minutes": round(time_in_bed_avg) if time_in_bed_avg is not None else None,
            "avg_formatted": _format_minutes(time_in_bed_avg),
        },
        "efficiency": {
            "avg_percent": round(sum(efficiencies) / len(efficiencies), 1) if efficiencies else None,
            "min_percent": round(min(efficiencies), 1) if efficiencies else None,
            "max_percent": round(max(efficiencies), 1) if efficiencies else None,
            "nights_with_efficiency_data": len(efficiencies),
        },
        "naps": {
            "total_nap_count": sum(nap_count_values) if nap_count_values else 0,
            "total_nap_duration_minutes": int(round(sum(nap_duration_values))) if nap_duration_values else 0,
            "total_nap_duration_formatted": _format_minutes(sum(nap_duration_values)) if nap_duration_values else "0m",
        },
        "physiology": {
            "avg_sleep_heart_rate_bpm": (
                round(sum(avg_sleep_hr_values) / len(avg_sleep_hr_values)) if avg_sleep_hr_values else None
            ),
            "nights_with_sleep_heart_rate": len(avg_sleep_hr_values),
        },
        "sleep_stage_totals_minutes": stage_totals if has_stage_values else None,
        "highlights": {
            "longest_sleep_night": longest_sleep_night,
            "shortest_sleep_night": shortest_sleep_night,
        },
    }


@sleep_router.tool
async def get_sleep_summary(
    user_id: str,
    start_date: str,
    end_date: str,
) -> dict:
    """
    Get daily sleep summaries for a user within a date range.

    This tool retrieves daily sleep summaries including start time, end time,
    duration, and sleep stages (if available from the wearable device).

    Args:
        user_id: UUID of the user. Use get_users to discover available users.
        start_date: Start date in YYYY-MM-DD format.
                    Example: "2026-01-01"
        end_date: End date in YYYY-MM-DD format.
                  Example: "2026-01-07"

    Returns:
        A dictionary containing:
        - user: Information about the user (id, first_name, last_name)
        - period: The date range queried (start, end)
        - records: List of sleep records with date, start_datetime, end_datetime, duration
        - summary: Aggregate statistics (avg_duration, total_nights, etc.)

    Example response:
        {
            "user": {"id": "uuid-1", "first_name": "John", "last_name": "Doe"},
            "period": {"start": "2026-01-05", "end": "2026-01-12"},
            "records": [
                {
                    "date": "2026-01-11",
                    "start_datetime": "2026-01-11T23:15:00+00:00",
                    "end_datetime": "2026-01-12T07:30:00+00:00",
                    "duration_minutes": 495,
                    "source": "whoop"
                }
            ],
            "summary": {
                "total_nights": 7,
                "nights_with_data": 6,
                "avg_duration_minutes": 465,
                "min_duration_minutes": 360,
                "max_duration_minutes": 540
            }
        }

    Notes for LLMs:
        - Call get_users first to get the user_id.
        - Calculate dates based on user queries:
          "last week" → start_date = 7 days ago, end_date = today
          "January 2026" → start_date = "2026-01-01", end_date = "2026-01-31"
        - Duration is in minutes.
        - The 'date' field is based on end_datetime (when the user woke up), not when they fell asleep.
        - start_datetime and end_datetime are full ISO 8601 timestamps. Sleep typically
          spans midnight, so end_datetime is often the day after start_datetime.
        - The 'source' field indicates which wearable provided the data (whoop, garmin, etc.)
    """
    try:
        # Fetch user details
        try:
            user_data = await client.get_user(user_id)
            user = _build_user_context(user_data)
        except ValueError as e:
            return {"error": f"User not found: {user_id}", "details": str(e)}

        # Fetch sleep data
        sleep_response = await client.get_sleep_summaries(
            user_id=user_id,
            start_date=start_date,
            end_date=end_date,
        )

        records_data = sleep_response.get("data", [])

        # Transform records
        records = []
        durations = []

        for record in records_data:
            duration = record.get("duration_minutes")
            if duration is not None:
                durations.append(duration)

            source = record.get("source", {})
            records.append(
                {
                    "date": str(record.get("date")),
                    "start_datetime": normalize_datetime(record.get("start_time")),
                    "end_datetime": normalize_datetime(record.get("end_time")),
                    "duration_minutes": duration,
                    "source": source.get("provider") if isinstance(source, dict) else source,
                }
            )

        # Calculate summary statistics
        summary = {
            "total_nights": len(records),
            "nights_with_data": len(durations),
            "avg_duration_minutes": None,
            "min_duration_minutes": None,
            "max_duration_minutes": None,
        }

        if durations:
            avg = sum(durations) / len(durations)
            summary.update(
                {
                    "avg_duration_minutes": round(avg),
                    "min_duration_minutes": min(durations),
                    "max_duration_minutes": max(durations),
                }
            )

        return {
            "user": user,
            "period": {"start": start_date, "end": end_date},
            "records": records,
            "summary": summary,
        }

    except ValueError as e:
        logger.error(f"API error in get_sleep_summary: {e}")
        return {"error": str(e)}
    except Exception as e:
        logger.exception(f"Unexpected error in get_sleep_summary: {e}")
        return {"error": f"Failed to fetch sleep summary: {e}"}


@sleep_router.tool
async def get_sleep_summaries_advanced(
    user_id: str,
    start_date: str,
    end_date: str,
    cursor: str | None = None,
    limit: int = 50,
) -> dict:
    """
    Get detailed sleep summaries with the full backend API payload and richer analysis.

    This is an advanced version of get_sleep_summary that preserves all fields returned by
    `/api/v1/users/{user_id}/summaries/sleep`, including stage breakdown, nap metrics,
    physiological metrics, and pagination/metadata blocks.

    Args:
        user_id: UUID of the user. Use get_users to discover available users.
        start_date: Start date in YYYY-MM-DD format.
        end_date: End date in YYYY-MM-DD format.
        cursor: Optional pagination cursor from a previous call.
        limit: Records per page (1-100, defaults to 50).

    Returns:
        A dictionary containing:
        - user: User context (id, first_name, last_name)
        - period: Query boundaries (start, end)
        - records: Full sleep records from API with normalized datetimes and formatted durations
        - pagination: Raw pagination object from API
        - metadata: Raw metadata object from API with normalized datetimes
        - analysis: Derived aggregates to help interpret sleep quality and trends

    Notes for LLMs:
        - Use this tool when you need all sleep fields instead of a compact summary.
        - The response preserves API fields like time_in_bed_minutes, stages, nap_count, and avg_heart_rate_bpm.
        - Use pagination.next_cursor when pagination.has_more is true.
        - Sleep `date` corresponds to wake-up day (based on end_time), not bedtime.
    """
    if limit < 1 or limit > 100:
        return {"error": "limit must be between 1 and 100"}

    try:
        try:
            user_data = await client.get_user(user_id)
            user = _build_user_context(user_data)
        except ValueError as e:
            return {"error": f"User not found: {user_id}", "details": str(e)}

        sleep_response = await client.get_sleep_summaries(
            user_id=user_id,
            start_date=start_date,
            end_date=end_date,
            cursor=cursor,
            limit=limit,
        )

        raw_records = sleep_response.get("data", [])
        records = [_normalize_sleep_record(record) for record in raw_records if isinstance(record, dict)]

        metadata_raw = sleep_response.get("metadata")
        metadata: dict[str, Any] | None
        if isinstance(metadata_raw, dict):
            metadata = {
                **metadata_raw,
                "start_time": normalize_datetime(metadata_raw.get("start_time")),
                "end_time": normalize_datetime(metadata_raw.get("end_time")),
            }
        else:
            metadata = None

        pagination_raw = sleep_response.get("pagination")
        pagination = pagination_raw if isinstance(pagination_raw, dict) else None

        return {
            "user": user,
            "period": {"start": start_date, "end": end_date},
            "records": records,
            "pagination": pagination,
            "metadata": metadata,
            "analysis": _compute_advanced_sleep_analysis(records),
        }

    except ValueError as e:
        logger.error(f"API error in get_sleep_summaries_advanced: {e}")
        return {"error": str(e)}
    except Exception as e:
        logger.exception(f"Unexpected error in get_sleep_summaries_advanced: {e}")
        return {"error": f"Failed to fetch advanced sleep summaries: {e}"}
