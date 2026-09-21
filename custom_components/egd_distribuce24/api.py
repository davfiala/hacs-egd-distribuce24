"""Small asynchronous EG.D client, independent of Home Assistant."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from zoneinfo import ZoneInfo

import aiohttp

PRAGUE = ZoneInfo("Europe/Prague")
TOKEN_URL = "https://idm.distribuce24.cz/oauth/token"
BASE_URL = "https://data.distribuce24.cz/rest"
VALID_STATUSES = frozenset({"W", "B", "E", "M", "N"})


class ApiError(Exception):
    """Sanitized API failure: never include response bodies or credentials."""


class AuthenticationError(ApiError):
    """Credentials were rejected."""


class AccessPeriodError(ApiError):
    """The account cannot read the entire requested historical period."""


@dataclass(frozen=True)
class Reading:
    timestamp: datetime
    value: Decimal | None
    status: str


def parse_readings(payload, ean: str, profile: str) -> list[Reading]:
    """Validate identity, units and samples before they enter statistics."""
    # Production returns a single object; the May 2026 manual shows a list.
    if isinstance(payload, dict):
        payload = [payload]
    if not isinstance(payload, list):
        raise ApiError("Unexpected measurement response")
    readings = {}
    try:
        for series in payload:
            if series["ean/eic"] != ean or series["profile"] != profile:
                raise ApiError("Unexpected meter or profile")
            if series["units"].lower() != "kwh":
                raise ApiError("Expected energy in kWh")
            for row in series["data"]:
                stamp = datetime.fromisoformat(row["timestamp"].replace("Z", "+00:00"))
                if stamp.tzinfo is None:
                    raise ApiError("Timestamp has no timezone")
                stamp = stamp.astimezone(UTC)
                if stamp.minute % 15 or stamp.second or stamp.microsecond:
                    raise ApiError("Unexpected measurement interval")
                value = None if row["value"] is None else Decimal(str(row["value"]))
                if value is not None and (not value.is_finite() or value < 0):
                    raise ApiError("Invalid energy value")
                reading = Reading(stamp, value, row["status"])
                if stamp in readings and readings[stamp] != reading:
                    raise ApiError("Conflicting duplicate measurement")
                readings[stamp] = reading
    except (KeyError, TypeError, ValueError, InvalidOperation) as err:
        raise ApiError("Malformed measurement response") from err
    return sorted(readings.values(), key=lambda item: item.timestamp)


class EgdClient:
    def __init__(self, session: aiohttp.ClientSession, client_id: str, client_secret: str):
        self._session = session
        self._client_id = client_id
        self._client_secret = client_secret
        self._token = None
        self._token_day = None
        self._lock = asyncio.Lock()

    async def _request(self, method, url, **kwargs):
        try:
            async with self._session.request(
                method,
                url,
                timeout=aiohttp.ClientTimeout(total=45),
                allow_redirects=False,
                **kwargs,
            ) as response:
                if response.status in (401, 403):
                    raise AuthenticationError("Authentication rejected")
                if response.status == 400 and url == TOKEN_URL:
                    raise AuthenticationError("Credentials rejected")
                if response.status == 400 and url == BASE_URL + "/spotreby":
                    try:
                        error = await response.json()
                    except (aiohttp.ClientError, ValueError):
                        error = {}
                    message = error.get("message", "") if isinstance(error, dict) else ""
                    if (
                        isinstance(error, dict)
                        and error.get("error") == "validation_error"
                        and isinstance(message, str)
                        and "období" in message
                        and "nemáte oprávnění" in message
                    ):
                        raise AccessPeriodError(
                            "EG.D: no access to the requested historical period"
                        )
                if response.status >= 300:
                    raise ApiError(f"EG.D HTTP {response.status}")
                return await response.json()
        except (aiohttp.ClientError, TimeoutError, ValueError) as err:
            raise ApiError("Unable to read EG.D response") from err

    async def _authenticate(self):
        today = datetime.now(PRAGUE).date()
        if self._token and self._token_day == today:
            return
        result = await self._request(
            "POST",
            TOKEN_URL,
            json={
                "grant_type": "client_credentials",
                "scope": "namerena_data_openapi",
                "client_id": self._client_id,
                "client_secret": self._client_secret,
            },
        )
        if not isinstance(result, dict) or not result.get("access_token"):
            raise ApiError("Missing access token")
        self._token, self._token_day = result["access_token"], today

    async def get(self, path, params=None):
        async with self._lock:
            for attempt in range(2):
                await self._authenticate()
                try:
                    return await self._request(
                        "GET",
                        BASE_URL + path,
                        params=params,
                        headers={"Authorization": f"Bearer {self._token}"},
                    )
                except AuthenticationError:
                    self._token = None
                    if attempt:
                        raise

    async def meters(self):
        result = await self.get("/om")
        if not isinstance(result, list) or any(
            not isinstance(row, dict)
            or not isinstance(row.get("ean"), str)
            or len(row["ean"]) != 18
            or not row["ean"].isdigit()
            or not isinstance(row.get("typMereni"), str)
            for row in result
        ):
            raise ApiError("Invalid meter list")
        return result

    async def readings(self, ean, profile, start, end):
        """Fetch recent data first, stopping at the account's historical boundary.

        On a period-permission error, shrink the window down to one day.
        Other HTTP 400 errors remain errors. A completely inaccessible recent
        period must fail, rather than pretending setup downloaded data.
        """
        result = {}
        had_access = False
        stop = end
        window = timedelta(days=28)
        while start < stop:
            begin = max(start, stop - window)
            try:
                payload = await self.get(
                    "/spotreby",
                    {
                        "ean": ean,
                        "profile": profile,
                        "from": begin.astimezone(UTC)
                        .isoformat(timespec="milliseconds")
                        .replace("+00:00", "Z"),
                        "to": (stop - timedelta(milliseconds=1))
                        .astimezone(UTC)
                        .isoformat(timespec="milliseconds")
                        .replace("+00:00", "Z"),
                    },
                )
            except AccessPeriodError:
                span = stop - begin
                if span <= timedelta(days=1):
                    if had_access:
                        break
                    raise
                window = timedelta(days=max(1, span.days // 2))
                continue
            had_access = True
            for reading in parse_readings(payload, ean, profile):
                if begin <= reading.timestamp < stop:
                    result[reading.timestamp] = reading
            stop = begin
        return sorted(result.values(), key=lambda item: item.timestamp)
