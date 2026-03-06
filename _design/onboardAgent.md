# Onboarding Agent — Design Document

## Overview
The Onboarding Agent is the first sub-agent users interact with. It collects a 9-field health profile through conversation, validates all inputs, saves data locally, and calculates initial nutrition targets (TDEE + macros).

It operates in two modes:
- **Agent Mode** — Natural conversation powered by FLock API (LLM brain)
- **Fallback Mode** — Step-by-step Q&A when FLock API is unavailable

All user data stays **local** (`data/profiles/`). Zero-knowledge — nothing leaves the user's machine.

---

## Architecture

```
┌─────────────────────────────────────────────────────────┐
│               User (Telegram / Discord / etc.)          │
└──────────────────────┬──────────────────────────────────┘
                       │ message
                       ▼
┌──────────────────────────────────────────────────────────┐
│       Personal Agent Orchestrator                        │
│       (agents/orchestrator.py)                           │
│                                                          │
│  Per-user personal agent instance                        │
│  detect_intent() → routes to appropriate handler         │
│  Modes: Telegram polling | OpenClaw webhook              │
│  + Webhook Receiver on port 8200 (proactive alerts)      │
└──────────────────────┬──────────────────────────────────┘
                       │
          ┌────────────┼─────────────────┐
          ▼            ▼                 ▼
   ┌─────────────┐ ┌──────────┐ ┌──────────────┐
   │ Onboarding  │ │Nutrition │ │ Threat       │
   │   Agent     │ │  Agent   │ │ Handler      │
   └──────┬──────┘ └──────────┘ └──────────────┘
          │                           │
          │ uses shared tools          │ calls Layer 3
          ▼                           ▼
   ┌──────────────────────────────┐  ┌──────────────────┐
   │       Shared Tools           │  │ Threat Backend   │
   │  validators, profile_manager │  │ localhost:8100   │
   │  macro_calculator, meal_*    │  │ (Layer 3)        │
   │  circuit_breaker             │  └──────────────────┘
   └──────────────────────────────┘
          │
          ▼
   ┌──────────────────┐     ┌──────────────────┐
   │  FLock API        │     │  Local Disk       │
   │  api.flock.io/v1  │     │  data/profiles/   │
   │  (LLM brain)      │     │  data/meals/      │
   └──────────────────┘     └──────────────────┘
```

---

## Files

| File | Lines | Purpose |
|------|-------|---------|
| `agents/orchestrator.py` | ~600 | Per-user personal agent + intent routing + Telegram polling + webhook receiver (port 8200) + proactive alert handler + auto-subscribe |
| `agents/onboarding_agent.py` | ~660 | Main onboarding logic: FLock agent mode + fallback |
| `agents/nutrition_agent.py` | ~410 | TDEE/macros, meal plans, meal logging, threat-adapted planning |
| `agents/tools/validators.py` | ~155 | 9 field validators + registry + helpers |
| `agents/tools/profile_manager.py` | ~200 | Profile CRUD, partial saves, cross-channel linking |
| `agents/tools/macro_calculator.py` | ~100 | Mifflin-St Jeor TDEE + macro splits |
| `agents/tools/meal_planner.py` | — | Meal plan generation logic |
| `agents/tools/meal_manager.py` | — | Meal logging + tracking |
| `agents/tools/circuit_breaker.py` | — | Fault tolerance for external API calls |

---

## Profile Fields (9 required)

| Field | Type | Validation | Example |
|-------|------|------------|---------|
| `name` | string | 2-50 chars, letters/spaces/hyphens | Sarah |
| `age` | int | 13-120, strips "years old" etc. | 28 |
| `sex` | enum | male / female | female |
| `weight` | float | 20-300 kg, strips units | 60.0 |
| `height` | float | 100-250 cm, strips units | 165.0 |
| `allergies` | string | comma-separated list or "none" | peanuts, dairy |
| `diet` | enum | mediterranean / keto / vegan / standard | vegan |
| `goal` | enum | cut / bulk / maintain | maintain |
| `city` | string | 2-100 chars, letters/spaces | London |

---

## Onboarding Flow

### Agent Mode (FLock API available)

```
User: "Hi! I'm Sarah, 28, trying to lose weight"
  │
  ▼
FLock API extracts: {name: "Sarah", age: "28", goal: "cut"}
  │
  ▼
Validators verify each field → save_partial()
  │
  ▼
Agent replies naturally: "Nice to meet you Sarah! I've got your name, age, and goal.
What's your biological sex? And how about your weight and height?"
  │
  ▼
(continues until all 9 fields collected)
  │
  ▼
Show summary → User confirms "yes"
  │
  ▼
save_profile() → calculate_macros() → show results + link code
```

### Fallback Mode (FLock unavailable)

```
Bot: "What's your name?"
User: "Sarah"
  │
  ▼
validate_name("Sarah") → OK → save_partial()
  │
  ▼
Bot: "Got it! [==-------] 1/9\n\nHow old are you?"
  │
  ▼
(one field at a time, with progress bar)
  │
  ▼
All 9 done → summary → confirm → save
```

### Circuit Breaker
- After 3 consecutive FLock API failures, the agent stops calling FLock
- Automatically switches to fallback mode for the rest of the session
- Prevents wasted API calls and latency when FLock is down
- Resets on bot restart

---

## FLock API Integration

### Config
```
FLOCK_API_KEY=sk-...
FLOCK_BASE_URL=https://api.flock.io/v1
FLOCK_MODEL=qwen3-30b-a3b-instruct-2507
```

### System Prompt Strategy
The system prompt is **dynamically rebuilt** on every message with:
- List of ALL required fields
- Which fields are already collected (JSON)
- Which fields are still missing
- Strict instruction to respond with JSON only: `{"extracted": {...}, "reply": "..."}`

### Token Efficiency
- `max_tokens: 512` — keeps replies concise
- `temperature: 0.7` — balances creativity and accuracy
- Only last 10 conversation turns sent as context
- System prompt includes already-collected fields so LLM doesn't re-ask

### Response Format
FLock returns structured JSON:
```json
{
  "extracted": {"name": "Sarah", "age": "28"},
  "reply": "Nice to meet you Sarah! What's your weight and height?",
  "complete": true  // only when all 9 fields collected
}
```

---

## Profile Persistence

### Storage Layout
```
data/profiles/
├── 7599032986.json          # Complete profile (Telegram chat_id)
├── 7599032986.partial.json  # Partial profile (onboarding in progress)
└── links.json               # Cross-channel identity map
```

### Profile Lifecycle
1. **Partial** — created on first field, updated on each new field
2. **Confirmation** — all 9 fields collected, user sees summary
3. **Complete** — user confirms, partial deleted, full profile saved
4. **Living** — any agent can update fields at any time post-onboarding

### Profile JSON Schema (complete)
```json
{
  "user_id": "7599032986",
  "profile": {
    "name": "Sarah",
    "age": "28",
    "sex": "female",
    "weight": "60.0",
    "height": "165.0",
    "allergies": "peanuts",
    "diet": "vegan",
    "goal": "maintain",
    "city": "London"
  },
  "link_code": "BDN-5A57-9A4B",
  "created_at": "2026-03-04T12:00:00+00:00",
  "updated_at": "2026-03-04T12:05:00+00:00",
  "version": "2.0"
}
```

---

## Cross-Channel Profile Linking

Users can share one profile across Telegram, Discord, WhatsApp, etc.

### How it works
1. After onboarding completes, user gets a **link code**: `BDN-5A57-9A4B`
2. On another channel, user types: `/link BDN-5A57-9A4B`
3. `links.json` maps the secondary channel ID to the primary user ID
4. All agents read/write to the same profile via `resolve_user_id()`

### links.json
```json
{
  "discord_123456": "7599032986",
  "slack_789012": "7599032986"
}
```

---

## Commands

| Command | Handler | Description |
|---------|---------|-------------|
| `/start` | `cmd_start()` | Begin or resume onboarding |
| `/reset` | `cmd_reset()` | Delete profile and start over |
| `/profile` | `cmd_profile()` | View current profile + link code |
| `/plan` | `cmd_plan()` | Calculate and show macro targets |
| `/threats` | `cmd_threats()` | Check local health threats (placeholder) |
| `/link <code>` | `cmd_link()` | Link this channel to existing profile |
| `/help` | `cmd_help()` | Show all available commands |

---

## Macro Calculation

Uses **Mifflin-St Jeor** equation:
- Male BMR: `10 × weight + 6.25 × height - 5 × age + 5`
- Female BMR: `10 × weight + 6.25 × height - 5 × age - 161`
- TDEE: `BMR × activity_multiplier` (default: moderate = 1.55)

### Goal Adjustments
| Goal | Calorie Adjustment | Protein | Carbs | Fat |
|------|-------------------|---------|-------|-----|
| Cut | -500 kcal | 40% | 30% | 30% |
| Bulk | +400 kcal | 30% | 45% | 25% |
| Maintain | 0 | 30% | 40% | 30% |

---

## Orchestrator

### Intent Detection
Rule-based slash commands + keyword matching + LLM-assisted for ambiguity:
1. Slash commands (`/threats`, `/meal`, `/profile`, `/help`, `/start`, `/reset`, `/link`, `/plan`) → route directly
2. No profile exists → onboarding
3. Partial profile → continue onboarding
4. Update keywords → onboarding (for field edits)
5. Meal/food/nutrition keywords → nutrition agent
6. Threat/safety/health keywords → threat handler
7. Default → nutrition agent (post-onboarding)

### Two Run Modes
1. **Telegram Polling** — `python -m agents.orchestrator` — standalone bot (MVP)
2. **Webhook** — `process_webhook(payload)` — called by OpenClaw gateway

### Webhook Receiver (port 8200)
- FastAPI server started alongside Telegram polling
- Receives proactive threat alerts from Layer 3
- Endpoint: `POST /threat-alert`
- On alert: Push to all users in that city → auto-chain to nutrition agent for meal adaptation

### Auto-Subscribe on Profile Complete
When onboarding finishes and user's city is set:
1. `_ensure_subscribed(user_id, city)` registers the city with Layer 3 (`POST /subscribe`)
2. User is added to `_city_users[city]` and `_user_cities[user_id]` registries
3. From this point, user automatically receives proactive threat alerts for their city
4. If user changes city later, the subscription updates accordingly

### Active Agents
- **`onboarding_agent.py`** — Natural profile collection (FLock-powered + fallback)
- **`nutrition_agent.py`** — TDEE/macros, meal plans, logging, threat-adapted meals
- **Threat Handler** (in orchestrator) — Calls Layer 3, forwards reports, chains to nutrition

---

## Key Design Decisions

| Decision | Choice | Rationale |
|----------|--------|-----------|
| LLM provider | FLock API | Hackathon requirement, OpenAI-compatible |
| Fallback strategy | Step-by-step mode | Bot works even without LLM |
| Data storage | Local JSON files | Privacy-first, zero-knowledge |
| Profile updates | Anytime, any agent | Living document, not one-time snapshot |
| Cross-channel | Link codes (BDN-XXXX) | No extra PII needed |
| Session persistence | Disk (no timeout) | User can resume days later |
| Validators | Always run (even with LLM) | Safety net — LLM output verified |
| Circuit breaker | 3 failures → skip FLock | Don't waste calls on broken API |
