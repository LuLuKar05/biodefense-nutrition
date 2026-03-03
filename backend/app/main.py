from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

app = FastAPI(title="Biodefense Nutrition API", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# -- Routers --
from app.routers import users, threats, webhooks

app.include_router(users.router, prefix="/api/users", tags=["Users"])
app.include_router(threats.router, prefix="/api/threats", tags=["Threats"])
app.include_router(webhooks.router, tags=["Webhooks"])


@app.get("/health")
async def health():
    return {"status": "ok"}
