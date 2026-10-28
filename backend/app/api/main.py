"""REST API for the dashboard. Run with: uvicorn app.api.main:app"""
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api import auth, events, fleet, kpis
from app.db import make_engine


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Connects as edgefleet_api: not the table owner, no BYPASSRLS.
    app.state.engine = make_engine(os.environ["DATABASE_URL"])
    yield
    await app.state.engine.dispose()


app = FastAPI(title="edgefleet-sim API", version="0.3.0", lifespan=lifespan)
app.include_router(auth.router)
app.include_router(fleet.router)
app.include_router(events.router)
app.include_router(kpis.router)


@app.get("/healthz", tags=["ops"])
async def healthz() -> dict[str, str]:
    return {"status": "ok"}
