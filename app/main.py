"""FastAPI app factory."""

import logging
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from langchain_core.language_models import BaseChatModel

from app.agent.runner import ChatRunner
from app.agent.tools import build_tools
from app.api import chat
from app.config import Settings, get_settings
from app.db.mongo import connect, ensure_indexes
from app.db.repositories import MessageRepo, SessionRepo
from app.llm.factory import get_chat_model
from app.services.market_api import MarketAPI

log = logging.getLogger("app")


def create_app(settings: Settings | None = None, chat_model: BaseChatModel | None = None) -> FastAPI:
    """`chat_model` overrides the provider from settings (tests pass a fake model)."""
    settings = settings or get_settings()
    logging.basicConfig(level=settings.log_level.upper(), format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        # Build the model first: a missing key should fail before anything connects.
        model = chat_model or get_chat_model(settings)

        mongo = connect(settings.mongodb_uri)
        db = mongo[settings.mongodb_db]
        await ensure_indexes(db, settings.session_ttl_days)

        http = httpx.AsyncClient(
            base_url=settings.edgemarket_api_base_url.rstrip("/"),
            timeout=httpx.Timeout(settings.http_timeout_s, connect=5.0),
            limits=httpx.Limits(max_connections=50, max_keepalive_connections=20),
            headers={"User-Agent": "edgemarket-chatbot/1.0", "Accept": "application/json"},
            follow_redirects=True,
        )
        market_api = MarketAPI(http, settings.edgemarket_site_url, settings.stats_cache_s)

        app.state.mongo = mongo
        app.state.db = db
        app.state.deps = chat.ChatDeps(
            settings=settings,
            sessions=SessionRepo(db, settings.session_ttl_days),
            messages=MessageRepo(db),
            runner=ChatRunner(model, build_tools(market_api), max_tool_calls=settings.max_tool_calls_per_turn),
        )
        log.info("Chatbot ready: provider=%s model=%s db=%s", settings.llm_provider, settings.llm_model, settings.mongodb_db)
        try:
            yield
        finally:
            await http.aclose()
            await mongo.close()

    app = FastAPI(title="EdgeMarket Chatbot", version="1.0.0", lifespan=lifespan)
    app.state.settings = settings

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
        allow_headers=["Content-Type", "X-Client-Id"],
        expose_headers=["X-session-id"],
    )

    app.include_router(chat.router)

    @app.get("/health")
    async def health(request: Request):
        try:
            await request.app.state.db.command("ping")
            mongo_ok = True
        except Exception:
            log.exception("Mongo ping failed")
            mongo_ok = False
        return JSONResponse(
            status_code=200 if mongo_ok else 503,
            content={
                "status": "ok" if mongo_ok else "degraded",
                "mongo": "ok" if mongo_ok else "error",
                "provider": settings.llm_provider,
                "model": settings.llm_model,
                "env": settings.app_env,
            },
        )

    return app


app = create_app()
