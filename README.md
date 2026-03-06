# Biodefense Nutrition

> Privacy-first platform combining personalised nutrition, bioinformatics, and decentralised AI to defend against emerging health threats.

Built for the **OpenClaw Hackathon 2026**.

---

## Architecture

```
User (Telegram / Discord / WhatsApp / …)
    │
    ▼
OpenClaw Gateway (:18789) ── multi-channel connector
    │
    ▼
Gateway Bridge (:18790) ── routes to agent orchestrator
    │
    ▼
Agent Orchestrator ── intent routing
    ├── Onboarding Agent  (profile collection)
    ├── Nutrition Agent   (meal plans, macros)
    └── Threat Agent      (outbreak alerts)
            │
            ▼
Threat Intelligence Backend (:8100) ── zero-knowledge API
    ├── WHO DON outbreaks
    ├── AQI monitoring
    └── Amina CLI Pipeline (cloud GPU bioinformatics)
```

### Three-Layer Privacy Model

| Layer | Component | Data |
|-------|-----------|------|
| 1 | OpenClaw Gateway | Message routing only |
| 2 | Agent Orchestrator | User profiles (local only, never leaves device) |
| 3 | Threat Backend | Public data only — **zero user data** |

---

## Amina CLI Integration

The system uses **6 Amina tools** via cloud GPU for real protein engineering:

| Tool | Category | Purpose |
|------|----------|---------|
| `esmfold` | Folding | Predict 3D protein structure from sequence |
| `pdb-cleaner` | Utilities | Clean PDB for downstream analysis |
| `pdb-quality-assessment` | Utilities | Validate structure quality (Ramachandran) |
| `p2rank` | Interactions | Predict ligand-binding pockets |
| `sasa` | Analysis | Solvent accessible surface area |
| `diffdock` | Interactions | AI molecular docking (compound screening) |

### Pipeline Flow

```
NCBI Protein Sequence
    │
    ├─→ Phase 2: Amina AI sequence analysis (motifs, composition)
    │
    ├─→ Phase 3a: esmfold → .pdb structure
    │       │
    │       ├─→ Phase 3b: pdb-cleaner → cleaned .pdb
    │       ├─→ Phase 3c: pdb-quality-assessment → validation
    │       ├─→ Phase 3d: p2rank → binding pockets  (parallel)
    │       ├─→ Phase 3e: sasa → surface area       (parallel)
    │       │
    │       ▼
    │   Phase 4: diffdock → dock 15 phytochemicals
    │       │
    │       ▼
    │   Merge: 40% sequence + 60% docking scores
    │
    ▼
Phase 5: FLock LLM → nutrition strategy JSON
```

---

## Quick Start

### Prerequisites

- Python 3.12+
- Node.js 22+ (for OpenClaw Gateway)
- Amina CLI API key ([get one free](https://app.aminoanalytica.com/settings/api))

### Setup

```bash
# 1. Clone and install
git clone https://github.com/LuLuKar05/biodefense-nutrition.git
cd biodefense-nutrition
pip install -r requirements.txt

# 2. Configure environment
cp .env.example .env
# Edit .env with your API keys

# 3. Authenticate Amina CLI
amina auth set-key <your-api-key>
```

### Run — Option A: Standalone (Telegram only)

```powershell
.\start_standalone.ps1
```

Launches:
- Layer 3 — Threat Backend on port 8100
- Orchestrator — Telegram polling + webhook receiver on port 8200

### Run — Option B: Multi-channel (via OpenClaw Gateway)

```powershell
.\start_gateway.ps1
```

Launches:
- Layer 3 — Threat Backend on port 8100
- Gateway Bridge on port 18790
- OpenClaw Gateway on port 18789

---

## Project Structure

```
OpenClawHack/
├── agents/                     # Layer 2 — Personal agents
│   ├── orchestrator.py         #   Intent router & Telegram polling
│   ├── onboarding_agent.py     #   Profile collection agent
│   ├── nutrition_agent.py      #   Meal planning agent
│   └── tools/                  #   Shared agent utilities
│       ├── profile_manager.py  #     User profile CRUD
│       ├── meal_planner.py     #     Meal plan generation
│       ├── macro_calculator.py #     TDEE & macro calculation
│       ├── meal_manager.py     #     Meal logging
│       ├── validators.py       #     Input validation
│       └── circuit_breaker.py  #     Fault tolerance
│
├── threat_backend/             # Layer 3 — Threat intelligence
│   ├── server.py               #   FastAPI endpoints
│   ├── research_agent.py       #   Full bioinformatics pipeline
│   ├── amina_ai.py             #   Amino acid analysis engine
│   ├── outbreak_fetcher.py     #   WHO DON integration
│   ├── outbreak_mock.py        #   Simulated outbreak data
│   ├── nutrient_mapper.py      #   Disease → nutrient mapping
│   ├── sequence_fetcher.py     #   NCBI protein sequences
│   ├── aqi_fetcher.py          #   Air quality data
│   └── cities.py               #   UK city database
│
├── gateway_bridge.py           # OpenClaw ↔ Orchestrator bridge
│
├── data/                       # Runtime data (gitignored)
│   ├── phytochemicals.json     #   15-compound library with SMILES
│   ├── disease_nutrition_db.json # Known disease strategies
│   ├── meal_templates.json     #   Meal plan templates
│   ├── structures/             #   PDB files from ESMFold
│   ├── analysis/               #   P2Rank, SASA, quality reports
│   ├── docking_results/        #   DiffDock output
│   └── profiles/               #   User profiles (local only)
│
├── dashboard/                  # Next.js system dashboard
├── backend/                    # Docker backend (Celery workers)
├── openclaw/                   # OpenClaw gateway config
├── tests/                      # Test suite
│
├── .env.example                # Environment template
├── requirements.txt            # Python dependencies
├── docker-compose.yml          # Docker services
├── start_standalone.ps1        # Option A launcher
└── start_gateway.ps1           # Option B launcher
```

---

## API Keys Required

| Service | Required | Free Tier | Purpose |
|---------|----------|-----------|---------|
| **FLock API** | Yes | Free | LLM brain for all agents |
| **Amina CLI** | Yes | $5 free | Cloud GPU protein tools |
| **Telegram** | Yes* | Free | Bot messaging (*or use other channels) |
| **NCBI** | Optional | Free | Faster protein lookups |
| **OpenWeatherMap** | Optional | Free | AQI data |

---

## Tech Stack

- **Python 3.12+** — FastAPI, httpx, asyncio
- **FLock API** — LLM (Qwen3-30B) for natural conversation
- **Amina CLI** — Cloud GPU protein folding & docking
- **OpenClaw** — Multi-channel gateway (22+ platforms)
- **Next.js** — System dashboard
- **Docker** — Backend services

---

## License

Built for the OpenClaw Hackathon 2026.
