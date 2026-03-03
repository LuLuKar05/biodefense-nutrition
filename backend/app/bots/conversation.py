"""Shared conversation flow for all bot platforms.

Handles: onboarding, meal logging, meal plan queries, threat alerts.
"""

# Onboarding steps (shared across Telegram & Discord):
ONBOARDING_STEPS = [
    {"key": "name", "prompt": "👋 Welcome! What's your name?"},
    {"key": "age", "prompt": "How old are you?"},
    {"key": "gender", "prompt": "What's your gender? (male/female)"},
    {"key": "allergies", "prompt": "Do you have any food allergies? (type them or say 'none')"},
    {"key": "diet_type", "prompt": "What's your preferred diet type?", "options": ["Standard", "Vegan", "Vegetarian", "Keto", "Mediterranean", "Paleo"]},
    {"key": "weight_kg", "prompt": "What's your current weight in kg?"},
    {"key": "height_cm", "prompt": "What's your height in cm?"},
    {"key": "activity_level", "prompt": "How active are you?", "options": ["Sedentary", "Light", "Moderate", "Active"]},
    {"key": "body_goal", "prompt": "What's your body goal?", "options": ["Cut", "Bulk", "Maintain"]},
    {"key": "location", "prompt": "What city or zip code are you in? (for threat monitoring)"},
]


async def handle_message(platform: str, user_id: str, text: str) -> str:
    """Process an incoming message and return a response.

    Args:
        platform: "telegram" or "discord"
        user_id: Platform-specific user ID
        text: The message text from the user

    Returns:
        Response string to send back to the user.
    """
    # TODO: Implement conversation state machine
    # - Check if user is mid-onboarding → advance to next step
    # - Check if user is asking for meal plan → return today's plan
    # - Check if user is logging a meal → parse and store
    # - Otherwise → pass to Z.ai agent for general queries
    return "🚧 Bot is under construction. Stay tuned!"
