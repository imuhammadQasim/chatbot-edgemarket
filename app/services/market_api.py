"""EdgeMarket backend client and field mapping.

Market data is read through the same public endpoints the website uses, so the
bot's numbers match the page exactly:

    GET /api/cms/get/:id                 getGroupById: market + outcomeStats
    GET /api/cms/get?searchText=...      getAllGroups: market list
    GET /api/bet/offchain/:id            getOffChainBetsByEvent (needs eventId_type=BLOCKCHAIN
                                         to look the market up by its _id, not live_eventId)
"""

import logging
import re
import time
from datetime import UTC, datetime
from typing import Any

import httpx

from app.schemas import is_object_id

log = logging.getLogger(__name__)

SORTS = ("ending_soon", "new", "popular", "volume", "resolved")
TEXT_MAX = 300
_CACHE_MAX = 500


class MarketAPIError(Exception):
    """The backend could not be reached or answered with an error."""


# --------------------------------------------------------------------------
# Pure mapping helpers (unit tested)
# --------------------------------------------------------------------------


def clean_text(value: Any, limit: int = TEXT_MAX) -> str:
    """User-written text as one bounded line: no control characters, no newlines."""
    if value is None:
        return ""
    text = re.sub(r"[\x00-\x1f\x7f]+", " ", str(value))
    text = re.sub(r"\s+", " ", text).strip()
    return text if len(text) <= limit else text[: limit - 1] + "…"


def market_title(doc: dict[str, Any]) -> str:
    for info in (doc.get("event") or {}).get("eventsInfos") or []:
        title = (info or {}).get("data", {}).get("title")
        if title:
            return clean_text(title)
    return clean_text(doc.get("group")) or "Untitled market"


def outcome_titles(doc: dict[str, Any]) -> list[str]:
    return [
        clean_text((o or {}).get("data", {}).get("title"), 120)
        for o in (doc.get("event") or {}).get("possibleComes") or []
    ]


def slugify(title: str) -> str:
    """Same as the frontend: lower, non [a-z0-9] runs -> '-', trim dashes."""
    slug = re.sub(r"[^a-z0-9]+", "-", (title or "").lower()).strip("-")
    return slug or "statistics"


def market_url(doc: dict[str, Any], site_url: str) -> str:
    """`/:group/:title/statistics/:id`, built like MarketEvents.jsx does."""
    group = (doc.get("group_type") or "").lower() or "event"
    title = ""
    infos = (doc.get("event") or {}).get("eventsInfos") or []
    if infos:
        title = (infos[0] or {}).get("data", {}).get("title") or ""
    return f"{site_url.rstrip('/')}/{group}/{slugify(title)}/statistics/{doc.get('_id')}"


def parse_timestamp(value: Any) -> datetime | None:
    """ISO string or unix seconds, like useMarketState.toMillis."""
    if value is None or value == "":
        return None
    try:
        return datetime.fromtimestamp(float(value), tz=UTC)
    except (TypeError, ValueError, OverflowError, OSError):
        pass
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def iso(dt: datetime | None) -> str | None:
    return dt.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ") if dt else None


def derive_status(doc: dict[str, Any], now: datetime) -> str:
    """open | betting_closed | awaiting_result | resolved | refunded.

    - refunded / resolved come from the settlement fields; `pred_result` is the
      backend's own test for "result announced" (groupController isAnnounced).
    - open: before event_end_timestamp, the same check GetStatistics.jsx uses
      to decide whether predictions are still taken (ending early moves that
      timestamp, and isBetEnded is honoured too).
    - after that, useMarketState.js: validation not open yet is
      "Awaiting Validation" (betting_closed), validation open is
      "In Resolution" (awaiting_result).
    """
    if doc.get("resolution") == "refunded":
        return "refunded"
    if str(doc.get("pred_result") or "").strip():
        return "resolved"

    ends_at = parse_timestamp(doc.get("event_end_timestamp"))
    if not doc.get("isBetEnded") and ends_at is not None and now < ends_at:
        return "open"

    validation_at = parse_timestamp(doc.get("validation_start_timestamp"))
    if validation_at is not None and now >= validation_at:
        return "awaiting_result"
    return "betting_closed"


def map_details(doc: dict[str, Any], site_url: str, now: datetime) -> dict[str, Any]:
    details: dict[str, Any] = {
        "id": str(doc.get("_id")),
        "title": market_title(doc),
        "category": clean_text(doc.get("group_type"), 60),
        "outcomes": outcome_titles(doc),
        "ends_at": iso(parse_timestamp(doc.get("event_end_timestamp"))),
        "status": derive_status(doc, now),
        "result": clean_text(doc.get("pred_result"), 120) or None,
        "resolution_method": doc.get("resolutionMethod"),
        "accepted_tokens": [clean_text(t.get("name"), 20) for t in doc.get("accepted_tokens") or [] if t],
        "url": market_url(doc, site_url),
    }
    validation_at = parse_timestamp(doc.get("validation_start_timestamp"))
    if validation_at:
        details["validation_starts_at"] = iso(validation_at)
    if doc.get("resolution"):
        details["resolution"] = doc["resolution"]
    if doc.get("resolvedAt"):
        details["resolved_at"] = iso(parse_timestamp(doc["resolvedAt"]))
    return details


def map_stats(doc: dict[str, Any], now: datetime) -> dict[str, Any]:
    outcomes = []
    for stat in doc.get("outcomeStats") or []:
        outcomes.append(
            {
                "title": clean_text(stat.get("teamTitle"), 120),
                "participants": stat.get("participants", 0),
                "market_confidence_pct": stat.get("convictionPercent", 0),
                "market_liquidity_pct": stat.get("betonPercent", 0),
                "staked_vera": stat.get("betonAmount", 0),
            }
        )
    return {
        "market_id": str(doc.get("_id")),
        "title": market_title(doc),
        "status": derive_status(doc, now),
        "outcomes": outcomes,
        # Sum of per-outcome participants: the base Market Confidence is a share of.
        "total_participants": sum(o["participants"] for o in outcomes),
        "total_staked_vera": round(sum(o["staked_vera"] or 0 for o in outcomes), 2),
        "as_of": iso(now),
    }


def leading_outcome(doc: dict[str, Any]) -> dict[str, Any] | None:
    stats = [s for s in doc.get("outcomeStats") or [] if (s.get("convictionPercent") or 0) > 0]
    if not stats:
        return None
    top = max(stats, key=lambda s: s.get("convictionPercent") or 0)
    return {"title": clean_text(top.get("teamTitle"), 120), "market_confidence_pct": top.get("convictionPercent")}


def map_search_item(doc: dict[str, Any], site_url: str, now: datetime) -> dict[str, Any]:
    return {
        "id": str(doc.get("_id")),
        "title": market_title(doc),
        "category": clean_text(doc.get("group_type"), 60),
        "ends_at": iso(parse_timestamp(doc.get("event_end_timestamp"))),
        "status": derive_status(doc, now),
        "participants": doc.get("participantsCount"),
        "leading_outcome": leading_outcome(doc),
        "url": market_url(doc, site_url),
    }


def truncate_wallet(wallet: Any) -> str | None:
    if not wallet:
        return None
    wallet = str(wallet)
    return wallet if len(wallet) <= 8 else f"{wallet[:4]}…{wallet[-2:]}"


def map_activity(bet: dict[str, Any]) -> dict[str, Any]:
    """One off-chain prediction. Telegram ids and full wallets never leave this function."""
    return {
        "outcome": clean_text(bet.get("prediction"), 120),
        "amount": bet.get("amount"),
        # Off-chain predictions are the off-chain SIGNA ("beton") ones; outcomeStats counts them the same way.
        "currency": "VERA",
        "time": iso(parse_timestamp(bet.get("date"))),
        "wallet": truncate_wallet(bet.get("wallet")) or ("Telegram user" if bet.get("telegramUserId") else None),
    }


def escape_js_regex(text: str) -> str:
    """getAllGroups feeds searchText straight into `new RegExp`; escape it so it matches literally."""
    return re.sub(r"[.*+?^${}()|[\]\\/]", r"\\\g<0>", text)


# --------------------------------------------------------------------------
# HTTP client
# --------------------------------------------------------------------------


class MarketAPI:
    def __init__(self, client: httpx.AsyncClient, site_url: str, cache_ttl_s: float = 15.0):
        self.client = client
        self.site_url = site_url
        self.cache_ttl_s = cache_ttl_s
        self._cache: dict[str, tuple[float, dict[str, Any] | None]] = {}

    async def _get_json(self, path: str, params: dict[str, Any] | None = None) -> Any:
        try:
            response = await self.client.get(path, params=params)
        except httpx.HTTPError as exc:
            log.warning("EdgeMarket API %s failed: %s", path, exc)
            raise MarketAPIError("EdgeMarket API is unreachable") from exc
        if response.status_code == 404:
            return None
        if response.status_code >= 400:
            log.warning("EdgeMarket API %s -> %s %s", path, response.status_code, response.text[:200])
            raise MarketAPIError(f"EdgeMarket API returned {response.status_code}")
        if not response.content.strip():
            return None  # getGroupById answers 200 with an empty body for an unknown id
        try:
            return response.json()
        except ValueError as exc:
            raise MarketAPIError("EdgeMarket API returned invalid JSON") from exc

    async def get_market(self, market_id: str) -> dict[str, Any] | None:
        """Raw market document with outcomeStats, cached for cache_ttl_s. None if it does not exist."""
        if not is_object_id(market_id):
            raise ValueError("market_id must be a 24-character hex id")
        key = market_id.lower()
        hit = self._cache.get(key)
        if hit and time.monotonic() - hit[0] < self.cache_ttl_s:
            return hit[1]
        doc = await self._get_json(f"/api/cms/get/{key}")
        doc = doc if isinstance(doc, dict) and doc.get("_id") else None
        self._remember(key, doc)
        return doc

    def _remember(self, key: str, doc: dict[str, Any] | None) -> None:
        if len(self._cache) >= _CACHE_MAX:
            cutoff = time.monotonic() - self.cache_ttl_s
            self._cache = {k: v for k, v in self._cache.items() if v[0] >= cutoff}
            if len(self._cache) >= _CACHE_MAX:
                self._cache.clear()
        self._cache[key] = (time.monotonic(), doc)

    async def search(
        self, query: str | None, category: str | None, sort: str | None, limit: int
    ) -> list[dict[str, Any]]:
        params = {
            "page": 1,
            "itemsPerPage": limit,
            "searchText": escape_js_regex(query.strip()) if query and query.strip() else "",
            "eventCategory": (category or "").strip(),
            "sort": sort if sort in SORTS else "ending_soon",
            "shuffle": "false",
        }
        body = await self._get_json("/api/cms/get", params=params)
        if not isinstance(body, dict):
            return []
        return list(((body.get("data") or {}).get("items")) or [])[:limit]

    async def recent_activity(self, market_id: str) -> list[dict[str, Any]] | None:
        if not is_object_id(market_id):
            raise ValueError("market_id must be a 24-character hex id")
        body = await self._get_json(f"/api/bet/offchain/{market_id.lower()}", params={"eventId_type": "BLOCKCHAIN"})
        if not isinstance(body, dict):
            return None
        return list(body.get("data") or [])
