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

1. **Triage** - Claude AI analyzes the error, stack trace, and service context
2. **Classify** - Determines if it's FIXABLE, TRANSIENT, INFRA_ISSUE, or NEEDS_HUMAN
3. **Fix** - For fixable errors, reads local Nucleus repo and generates a code fix
4. **Review** - Optional CodeRabbit review with retry loop
5. **Test** - Optional sandbox testing in Kind cluster
6. **PR** - Creates a branch, commits the fix, and opens a draft PR via `git` + `gh` CLI

## Features

- **GCP Polling Mode** - Queries Cloud Logging API directly (read-only access required)
- **Real-time Dashboard** - React frontend with WebSocket updates, filter tabs, and detailed incident views
- **Smart Triage** - Recognizes transient errors (retries, rate limits, duplicate keys, etc.)
- **Firestore Persistence** - All incidents, triage results, fixes, and test results saved to Firestore
- **Local Git Workflow** - Reads files from local repo, creates PRs via `gh` CLI
- **Fuzzy Code Matching** - Handles whitespace differences in generated fixes
- **Status Filtering** - Filter incidents by All, Active, Fixed, or Review status

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

# 4. Start backend (default port 8000)
python -m uvicorn backend.main:app --host 0.0.0.0 --port 8000

# 5. Start frontend (in another terminal)
cd frontend && npm run dev

# 6. Open dashboard
open http://localhost:5173
```

## Environment Variables

Key configuration options in `.env`:

```bash
# Required
ANTHROPIC_API_KEY=sk-ant-...        # Claude AI
GCP_PROJECT_ID=nucleus-449303       # GCP project for Cloud Logging
GITHUB_TOKEN=github_pat_...         # GitHub Personal Access Token
GITHUB_REPO=tenex-eng/nucleus       # Target repository

# Storage (Firestore)
STORAGE_BACKEND=firestore           # 'memory' or 'firestore'
FIRESTORE_PROJECT_ID=on-call-helper-486123  # Can be different from GCP_PROJECT_ID

# Repository paths
NUCLEUS_REPO_PATH=/Users/you/nucleus
ONCALL_REPO_PATH=/Users/you/oncall
```

## Key Endpoints

| Endpoint | Description |
|----------|-------------|
| `GET /health` | Health check |
| `GET /incidents` | List all incidents (includes triage classification) |
| `GET /incidents/{id}` | Get incident with triage, fix, test, verification |
| `POST /webhook/test` | Send test incident |
| `POST /gcp/polling/start` | Start GCP log polling |
| `POST /gcp/polling/stop` | Stop GCP log polling |
| `GET /gcp/polling/status` | Check polling status |
| `WS /ws` | WebSocket for real-time updates |
| `GET /docs` | API documentation |

## Dashboard Features

The React dashboard provides:

- **Incident List** - All incidents with severity indicators and status badges
- **Filter Tabs** - Quick filters for All, Active, Fixed, Review
- **Detail Panel** - Full incident analysis including:
  - Classification badge (Auto-Fixable, Self-Healing, Infra Issue, Needs Review)
  - Root cause analysis from Claude AI
  - Affected service and file information
  - Related context from GCP logs
  - Error message and stack trace
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

## Example: Start GCP Polling

```bash
# Authenticate with GCP first
gcloud auth application-default login

# Start polling (every 30 seconds)
curl -X POST "http://localhost:8000/gcp/polling/start?interval_seconds=30"

# Check status
curl http://localhost:8000/gcp/polling/status

# Stop polling
curl -X POST http://localhost:8000/gcp/polling/stop
```

## Project Structure

```
on-call-helper/
├── backend/
│   ├── main.py              # FastAPI application
│   ├── config.py            # Environment configuration
│   ├── storage.py           # In-memory storage
│   ├── storage_firestore.py # Firestore persistence
│   ├── agents/
│   │   ├── triage.py        # Claude AI triage agent
│   │   ├── fixer.py         # Claude AI fix generator
│   │   └── orchestrator.py  # Pipeline coordinator
│   ├── services/
│   │   ├── gcp_logging.py   # GCP Cloud Logging integration
│   │   ├── github.py        # Git + gh CLI for PRs
│   │   ├── sandbox.py       # Kind cluster testing
│   │   └── coderabbit.py    # Code review integration
│   ├── filters/
│   │   ├── transient.py     # Transient error detection
│   │   └── tenant.py        # Demo tenant filtering
│   ├── models/
│   │   └── incident.py      # Pydantic data models
│   └── websocket_manager.py # Real-time event broadcasting
├── frontend/
│   ├── src/
│   │   ├── App.jsx          # Dashboard UI
│   │   └── context/         # State management
│   └── vite.config.js       # Dev server config (proxies to port 8000)
├── .env.example             # Environment template
├── requirements.txt         # Python dependencies
├── SETUP.md                 # Setup guide
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

## License

MIT
