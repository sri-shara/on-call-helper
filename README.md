# On Call Helper

AI-powered incident response agent for the Nucleus MDR platform. Automatically monitors GCP Cloud Logging for production errors, triages them using Claude AI, generates fixes, and creates PRs - reducing MTTR from hours to minutes.

## How It Works

```
┌─────────────────┐     ┌─────────────────┐     ┌─────────────────┐
│  GCP Cloud      │────>│   On Call       │────>│   GitHub PR     │
│  Logging        │     │   Helper        │     │   (via git)     │
└─────────────────┘     └────────┬────────┘     └─────────────────┘
                              │
      ┌───────────────────────┼───────────────────────┐
      │                       │                       │
      v                       v                       v
┌───────────────┐      ┌─────────────────┐     ┌───────────────┐
│ Claude AI     │      │ Local Nucleus   │     │ Firestore     │
│ (Triage/Fix)  │      │ Repository      │     │ (Persistence) │
└───────────────┘      └─────────────────┘     └───────────────┘
```

When an error is detected in GCP Cloud Logging:

1. **Pre-process** - Smart pipeline filters noise (K8s infra, demo tenants, transient errors)
2. **Triage** - Claude AI analyzes the error with historical pattern context
3. **Classify** - Determines if it's FIXABLE, TRANSIENT, INFRA_ISSUE, or NEEDS_HUMAN
4. **Fix** - For fixable errors, reads local Nucleus repo and generates a code fix
5. **Review** - Optional CodeRabbit review with retry loop (up to 3 iterations)
6. **Test** - Optional sandbox testing in Kind cluster
7. **PR** - Creates a branch, commits the fix, and opens a draft PR
8. **Verify** - Monitors production for 2 hours to confirm fix worked

## Features

### Core Capabilities
- **GCP Polling Mode** - Queries Cloud Logging API directly (read-only access required)
- **Smart Pre-processing** - Filters K8s noise, demo tenants, and transient errors
- **AI-Powered Triage** - Claude analyzes errors with embedded SRE knowledge
- **Pattern Learning** - Learns from historical incidents to improve classification accuracy
- **Automated Fix Generation** - Creates minimal, targeted code fixes
- **Production Verification** - Monitors for error recurrence after PR merge

### Dashboard & Real-time Updates
- **React Dashboard** - Filter by All, Active, Fixed, or Review status
- **WebSocket Updates** - Real-time incident progress and agent activity
- **Incident Details** - Full analysis, code diffs, test results, and PR links

### Storage & Persistence
- **Firestore Backend** - Persistent storage for incidents, results, and learned patterns
- **In-Memory Mode** - Fast development without GCP dependencies

## Quick Start

```bash
# 1. Clone and setup
git clone https://github.com/tenex-eng/on-call-helper.git
cd on-call-helper
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

# 2. Install frontend dependencies
cd frontend && npm install && cd ..

# 3. Configure environment
cp .env.example .env
# Edit .env with your API keys (see SETUP.md for details)

# 4. Start backend
python -m backend.main

# 5. Start frontend (in another terminal)
cd frontend && npm run dev

# 6. Open dashboard
open http://localhost:5173
```

## Environment Variables

Key configuration options in `.env`:

```bash
# Required - AI
ANTHROPIC_API_KEY=sk-ant-...        # Claude AI

# Required - GCP
GCP_PROJECT_ID=your-project         # GCP project for Cloud Logging
GCP_AUTO_POLL=true                  # Auto-start polling on startup
GCP_POLL_INTERVAL=30                # Polling interval in seconds

# Required - GitHub
GITHUB_TOKEN=github_pat_...         # GitHub Personal Access Token
GITHUB_REPO=your-org/nucleus        # Target repository

# Required - Repositories
NUCLEUS_REPO_PATH=/path/to/nucleus  # Local clone of target codebase
ONCALL_REPO_PATH=/path/to/oncall    # SRE knowledge/runbooks repo

# Storage (Firestore recommended for production)
STORAGE_BACKEND=firestore           # 'memory' or 'firestore'
FIRESTORE_PROJECT_ID=your-project   # Can differ from GCP_PROJECT_ID
FIRESTORE_DATABASE_ID=oncall-helper-db

# Pattern Learning
PATTERN_LEARNING_ENABLED=true
PATTERN_MIN_OCCURRENCES=3           # Min occurrences before override
PATTERN_OVERRIDE_SUCCESS_RATE=0.70  # Min success rate for override
PATTERN_OVERRIDE_CONFIDENCE=0.80    # Min confidence for override
```

## Key Endpoints

| Endpoint | Description |
|----------|-------------|
| `GET /health` | Health check |
| `GET /incidents` | List all incidents |
| `GET /incidents/{id}` | Get incident with triage, fix, test, verification |
| `POST /incidents/{id}/resolve` | Manually resolve an incident |
| `POST /webhook/test` | Send test incident |
| `POST /gcp/polling/start` | Start GCP log polling |
| `POST /gcp/polling/stop` | Stop GCP log polling |
| `GET /gcp/polling/status` | Check polling status |
| `GET /patterns/stats` | Pattern learning statistics |
| `GET /patterns/similar` | Find similar patterns for an error |
| `GET /history/summary` | Error summary for reporting |
| `WS /ws` | WebSocket for real-time updates |
| `GET /docs` | API documentation (Swagger UI) |

## Dashboard Features

The React dashboard provides:

- **Metrics Panel** - Total incidents, auto-fixed, escalated, MTTR
- **Filter Tabs** - Quick filters for All, Active, Fixed, Review
- **Incident List** - All incidents with severity and status badges
- **Detail Panel** - Full incident analysis including:
  - Classification badge (Auto-Fixable, Self-Healing, Infra Issue, Needs Review)
  - Root cause analysis from Claude AI
  - Historical pattern match (if found)
  - Affected service and file information
  - Generated code fix with before/after diff
  - Test results (if sandbox enabled)
  - Pull request link
  - Production verification status

## Example: Send a Test Incident

```bash
curl -X POST http://localhost:8000/webhook/test \
  -H "Content-Type: application/json" \
  -d '{
    "error_message": "nil pointer dereference in ProcessAlert",
    "service_name": "alertservice",
    "tenant_name": "Acme Corp",
    "file_path": "backend/services/alertservice/alert_service.go"
  }'
```

## Example: Check Pattern Learning

```bash
# View pattern statistics
curl http://localhost:8000/patterns/stats

# Find similar patterns for an error
curl "http://localhost:8000/patterns/similar?error=nil%20pointer&service=alertservice"
```

## Project Structure

```
on-call-helper/
├── backend/
│   ├── main.py              # FastAPI application
│   ├── config.py            # Environment configuration
│   ├── storage.py           # In-memory storage
│   ├── storage_firestore.py # Firestore persistence
│   ├── websocket_manager.py # Real-time event broadcasting
│   ├── agents/
│   │   ├── triage.py        # Claude AI triage agent
│   │   ├── fixer.py         # Claude AI fix generator
│   │   └── orchestrator.py  # Pipeline coordinator
│   ├── services/
│   │   ├── gcp_logging.py   # GCP Cloud Logging integration
│   │   ├── github.py        # Git + gh CLI for PRs
│   │   ├── sandbox.py       # Kind cluster testing
│   │   ├── coderabbit.py    # Code review integration
│   │   ├── production_monitor.py  # Post-merge verification
│   │   └── error_aggregator.py    # Error deduplication
│   ├── knowledge/
│   │   ├── error_patterns.py     # Known error patterns (47+)
│   │   ├── infrastructure.py     # GCP health checks
│   │   ├── tenants.py            # Tenant classification
│   │   ├── runbooks.py           # Runbook suggestions
│   │   └── pattern_learner.py    # Historical pattern learning
│   ├── filters/
│   │   ├── transient.py     # Transient error detection
│   │   ├── tenant.py        # Demo tenant filtering
│   │   └── service_filter.py # K8s noise filtering
│   └── models/
│       └── incident.py      # Pydantic data models
├── frontend/
│   ├── src/
│   │   ├── App.jsx          # Dashboard UI
│   │   ├── context/         # State management
│   │   ├── components/      # UI components
│   │   └── hooks/           # WebSocket hook
│   └── vite.config.js       # Dev server config
├── .env.example             # Environment template
├── requirements.txt         # Python dependencies
├── SETUP.md                 # Detailed setup guide
├── FUNCTIONALITY.md         # Feature documentation
└── ARCHITECTURE.md          # System design documentation
```

## Requirements

- Python 3.9+
- Node.js 18+
- GCP account with Cloud Logging read access
- Anthropic API key (for Claude AI)
- GitHub CLI (`gh`) authenticated
- Local clone of Nucleus repository
- (Optional) Firestore for persistent storage
- (Optional) Kind for sandbox testing

## Documentation

- [SETUP.md](SETUP.md) - Detailed installation and configuration guide
- [FUNCTIONALITY.md](FUNCTIONALITY.md) - Complete feature documentation
- [ARCHITECTURE.md](ARCHITECTURE.md) - System design and architecture

## License

MIT
