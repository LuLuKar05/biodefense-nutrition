from fastapi import APIRouter

router = APIRouter()


@router.post("/onboard")
async def onboard_user():
    """Phase 1: Create user profile with health data."""
    # TODO: Accept user profile JSON, store in MongoDB users collection
    return {"message": "onboard endpoint — not yet implemented"}


@router.get("/{user_id}/meal-plan")
async def get_meal_plan(user_id: str):
    """Phase 1/5: Get current (possibly adapted) meal plan."""
    # TODO: Return meal plan from MongoDB
    return {"message": "meal-plan endpoint — not yet implemented"}


@router.post("/{user_id}/log-meal")
async def log_meal(user_id: str):
    """Phase 1: Log a meal from bot chat input."""
    # TODO: Parse meal text, store nutrition data
    return {"message": "log-meal endpoint — not yet implemented"}


@router.post("/{user_id}/report-symptoms")
async def report_symptoms(user_id: str):
    """Phase 5: Submit symptom self-report for FLock."""
    # TODO: Store symptom report for federated learning
    return {"message": "report-symptoms endpoint — not yet implemented"}
