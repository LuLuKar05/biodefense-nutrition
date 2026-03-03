from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

app = FastAPI(
    title="Biodefense Nutrition — Threat Intelligence API",
    version="0.2.0",
    description="Zero-knowledge backend: serves ONLY public threat data. No user PII stored.",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# -- Routers (threat intelligence only — ZERO user data) --
from app.routers import threats, flock, dashboard as dashboard_router

app.include_router(threats.router, prefix="/api/threats", tags=["Threats"])
app.include_router(flock.router, prefix="/api/flock", tags=["FLock Federated"])
app.include_router(dashboard_router.router, prefix="/api/dashboard", tags=["Dashboard"])

# NOTE: There are NO /api/users/* endpoints.
# All user data stays on the user's device in OpenClaw's local session memory.
# This backend is a zero-knowledge threat intelligence service.


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok", "privacy": "zero-knowledge — no user data stored"}
