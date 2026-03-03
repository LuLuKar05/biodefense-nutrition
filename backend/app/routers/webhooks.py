from fastapi import APIRouter, Request

router = APIRouter()


@router.post("/webhook/telegram")
async def telegram_webhook(request: Request):
    """Handle incoming Telegram bot updates."""
    # TODO: Parse update, route to conversation handler
    body = await request.json()
    return {"ok": True}


@router.post("/webhook/discord")
async def discord_webhook(request: Request):
    """Handle incoming Discord interactions."""
    # TODO: Parse interaction, route to conversation handler
    body = await request.json()
    return {"ok": True}
