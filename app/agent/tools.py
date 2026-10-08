"""Agent tools. Each returns compact JSON, or a short "ERROR: ..." string; none raise into the stream."""

import functools
import json
import logging
from datetime import UTC, datetime
from typing import Literal

from langchain_core.tools import BaseTool, tool
from pydantic import BaseModel, Field

from app.agent.runner import TOOL_ERROR_PREFIX
from app.schemas import is_object_id
from app.services.market_api import (
    MarketAPI,
    MarketAPIError,
    map_activity,
    map_details,
    map_search_item,
    map_stats,
)

log = logging.getLogger(__name__)

BAD_ID = f"{TOOL_ERROR_PREFIX} market_id must be a 24-character hex id."
NOT_FOUND = f"{TOOL_ERROR_PREFIX} no market exists with that id."
UNAVAILABLE = f"{TOOL_ERROR_PREFIX} market data is temporarily unavailable. Tell the user to try again shortly."


def guarded(fn):
    """Market API failures and anything unexpected become an error string for the model."""

    @functools.wraps(fn)
    async def wrapper(*args, **kwargs):
        try:
            return await fn(*args, **kwargs)
        except MarketAPIError:
            return UNAVAILABLE
        except Exception:
            log.exception("Tool %s failed", fn.__name__)
            return f"{TOOL_ERROR_PREFIX} unexpected error while fetching market data."

    return wrapper


def _json(data) -> str:
    return json.dumps(data, ensure_ascii=False, separators=(",", ":"))


class MarketIdArgs(BaseModel):
    market_id: str = Field(description="24-character hex id of the market, e.g. viewing_market_id from <context>.")


class SearchArgs(BaseModel):
    query: str | None = Field(default=None, max_length=100, description="Words to look for in market titles.")
    category: str | None = Field(
        default=None, max_length=50, description="Category slug such as football, crypto, politics, meme."
    )
    sort: Literal["ending_soon", "new", "popular", "volume", "resolved"] = Field(
        default="ending_soon",
        description="ending_soon (default), new, popular (most participants; use for 'trending'), volume, resolved.",
    )
    limit: int = Field(default=5, ge=1, le=10, description="How many markets to return (1-10).")


class ActivityArgs(BaseModel):
    market_id: str = Field(description="24-character hex id of the market.")
    limit: int = Field(default=10, ge=1, le=20, description="How many recent predictions to return (1-20).")


def build_tools(api: MarketAPI) -> list[BaseTool]:
    def now() -> datetime:
        return datetime.now(UTC)

    @tool("get_market_details", args_schema=MarketIdArgs)
    @guarded
    async def get_market_details(market_id: str) -> str:
        """Question, outcomes, end time (UTC), status, result, resolution method, accepted tokens and page link of one market."""
        if not is_object_id(market_id):
            return BAD_ID
        doc = await api.get_market(market_id)
        if doc is None:
            return NOT_FOUND
        return _json(map_details(doc, api.site_url, now()))

    @tool("get_market_stats", args_schema=MarketIdArgs)
    @guarded
    async def get_market_stats(market_id: str) -> str:
        """Live per-outcome stats of one market: participants, Market Confidence %, Market Liquidity %, staked SIGNAL. Same numbers as the market page."""
        if not is_object_id(market_id):
            return BAD_ID
        doc = await api.get_market(market_id)
        if doc is None:
            return NOT_FOUND
        return _json(map_stats(doc, now()))

    @tool("search_markets", args_schema=SearchArgs)
    @guarded
    async def search_markets(
        query: str | None = None, category: str | None = None, sort: str = "ending_soon", limit: int = 5
    ) -> str:
        """Find EdgeMarket markets by keywords and/or category. Open markets are listed first, except with sort=resolved."""
        docs = await api.search(query, category, sort, limit)
        current = now()
        return _json({"count": len(docs), "markets": [map_search_item(d, api.site_url, current) for d in docs]})

    @tool("get_recent_activity", args_schema=ActivityArgs)
    @guarded
    async def get_recent_activity(market_id: str, limit: int = 10) -> str:
        """Most recent off-chain (SIGNAL) predictions on one market: outcome, amount, time and a shortened wallet."""
        if not is_object_id(market_id):
            return BAD_ID
        bets = await api.recent_activity(market_id)
        if bets is None:
            return NOT_FOUND
        limit = max(1, min(limit, 20))
        return _json({"count": min(len(bets), limit), "predictions": [map_activity(b) for b in bets[:limit]]})

    return [get_market_details, get_market_stats, search_markets, get_recent_activity]
