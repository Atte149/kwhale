from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .db import get_pool, close_pool
from .routers import auth, library, stream, events, recommendations, discover, vibe, internal, download, subsonic, search, online


@asynccontextmanager
async def lifespan(app: FastAPI):
    await get_pool()
    yield
    await close_pool()


app = FastAPI(
    title="KWhale API",
    version="1.0.0",
    description="Self-hosted music service with AI-powered recommendations, rich telemetry, and source plugins.",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth.router, prefix="/api")
app.include_router(library.router, prefix="/api")
app.include_router(stream.router, prefix="/api")
app.include_router(events.router, prefix="/api")
app.include_router(recommendations.router, prefix="/api")
app.include_router(discover.router, prefix="/api")
app.include_router(vibe.router, prefix="/api")
app.include_router(search.router, prefix="/api")
app.include_router(online.router, prefix="/api")
app.include_router(internal.router)
app.include_router(download.router)
# Subsonic protocol proxy — makes /rest/* work regardless of whether
# the client is pointed at the public host (Caddy → Navidrome) or at
# the kwhale API directly (this proxy → Navidrome).
app.include_router(subsonic.router)


@app.get("/healthz", tags=["health"])
async def health():
    return {"status": "ok"}


@app.get("/api", tags=["health"])
async def root():
    return {
        "service": "kwhale-api",
        "version": "1.0.0",
        "docs": "/docs",
        "openapi": "/openapi.json",
    }
