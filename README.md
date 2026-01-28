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
│ Claude AI     │      │ Local Nucleus   │     │ Real-time     │
│ (Triage/Fix)  │      │ Repository      │     │ Dashboard     │
└───────────────┘      └─────────────────┘     └───────────────┘
```

When an error is detected in GCP Cloud Logging:

1. **Triage** - Claude AI analyzes the error, stack trace, and service context
2. **Classify** - Determines if it's FIXABLE, TRANSIENT, INFRA_ISSUE, or NEEDS_HUMAN
3. **Fix** - For fixable errors, reads local Nucleus repo and generates a code fix
4. **Review** - Optional CodeRabbit review with retry loop
5. **PR** - Creates a branch, commits the fix, and opens a draft PR via `git` + `gh` CLI

## Features

- **GCP Polling Mode** - Queries Cloud Logging API directly (read-only access required)
- **Real-time Dashboard** - React frontend with WebSocket updates
- **Smart Triage** - Recognizes transient errors (retries, rate limits, etc.)
- **Local Git Workflow** - Reads files from local repo, creates PRs via `gh` CLI
- **Fuzzy Code Matching** - Handles whitespace differences in generated fixes

## Quick Start

```bash
# 1. Clone and setup
git clone https://github.com/your-org/on-call-helper.git
cd on-call-helper
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

# 2. Install frontend dependencies
cd frontend && npm install && cd ..

# 3. Configure environment
cp .env.example .env
# Edit .env with your API keys (see SETUP.md for details)

# 4. Start backend (port 8001 to avoid conflicts)
PORT=8001 python -m backend.main

# 5. Start frontend (in another terminal)
cd frontend && npm run dev

# 6. Open dashboard
open http://localhost:5173
```

## Key Endpoints

| Endpoint | Description |
|----------|-------------|
| `GET /health` | Health check |
| `GET /incidents` | List all incidents |
| `POST /webhook/test` | Send test incident |
| `POST /gcp/polling/start` | Start GCP log polling |
| `POST /gcp/polling/stop` | Stop GCP log polling |
| `GET /gcp/polling/status` | Check polling status |
| `WS /ws` | WebSocket for real-time updates |
| `GET /docs` | API documentation |

## Documentation

- **[SETUP.md](SETUP.md)** - Detailed setup and configuration guide
- **[ARCHITECTURE.md](ARCHITECTURE.md)** - System design documentation

## Example: Send a Test Incident

```bash
curl -X POST http://localhost:8001/webhook/test \
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
curl -X POST "http://localhost:8001/gcp/polling/start?interval_seconds=30"

# Check status
curl http://localhost:8001/gcp/polling/status

# Stop polling
curl -X POST http://localhost:8001/gcp/polling/stop
```

## Project Structure

```
on-call-helper/
├── backend/
│   ├── main.py              # FastAPI application
│   ├── config.py            # Environment configuration
│   ├── agents/
│   │   ├── triage.py        # Claude AI triage agent
│   │   ├── fixer.py         # Claude AI fix generator
│   │   └── orchestrator.py  # Pipeline coordinator
│   ├── services/
│   │   ├── gcp_logging.py   # GCP Cloud Logging integration
│   │   ├── github.py        # Git + gh CLI for PRs
│   │   └── coderabbit.py    # Code review integration
│   └── websocket_manager.py # Real-time event broadcasting
├── frontend/
│   ├── src/
│   │   ├── App.jsx          # Dashboard UI
│   │   └── context/         # State management
│   └── vite.config.js       # Dev server config
├── .env.example             # Environment template
├── requirements.txt         # Python dependencies
└── SETUP.md                 # Setup guide
```

## Requirements

- Python 3.9+
- Node.js 18+
- GCP account with Cloud Logging read access
- Anthropic API key (for Claude AI)
- GitHub CLI (`gh`) authenticated
- Local clone of Nucleus repository

## License

MIT
