#!/bin/bash
# On Call Helper - Setup Script
# Initializes the development environment

set -e

echo "==================================="
echo "On Call Helper - Setup"
echo "==================================="
echo ""

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Check for required tools
check_tool() {
    if ! command -v "$1" &> /dev/null; then
        echo -e "${RED}Error: $1 is not installed${NC}"
        return 1
    fi
    echo -e "${GREEN}✓${NC} $1 found"
    return 0
}

echo "Checking required tools..."
echo ""

MISSING_TOOLS=0

check_tool "python3" || MISSING_TOOLS=1
check_tool "pip3" || check_tool "pip" || MISSING_TOOLS=1
check_tool "node" || MISSING_TOOLS=1
check_tool "npm" || MISSING_TOOLS=1
check_tool "docker" || echo -e "${YELLOW}Warning: Docker not found (optional for local dev)${NC}"
check_tool "kind" || echo -e "${YELLOW}Warning: Kind not found (optional - needed for sandbox testing)${NC}"

if [ $MISSING_TOOLS -eq 1 ]; then
    echo ""
    echo -e "${RED}Please install the missing required tools and try again${NC}"
    exit 1
fi

echo ""
echo "Setting up Python environment..."
echo ""

# Create virtual environment if it doesn't exist
if [ ! -d "venv" ]; then
    echo "Creating virtual environment..."
    python3 -m venv venv
fi

# Activate virtual environment
source venv/bin/activate

# Upgrade pip
echo "Upgrading pip..."
pip install --upgrade pip --quiet

# Install Python dependencies
echo "Installing Python dependencies..."
pip install -r requirements.txt --quiet

echo -e "${GREEN}✓${NC} Python dependencies installed"

echo ""
echo "Setting up frontend..."
echo ""

# Install frontend dependencies
cd frontend
if [ ! -d "node_modules" ]; then
    echo "Installing npm dependencies..."
    npm install --silent
else
    echo -e "${GREEN}✓${NC} Frontend dependencies already installed"
fi

# Build frontend
echo "Building frontend..."
npm run build --silent

echo -e "${GREEN}✓${NC} Frontend built"

cd ..

echo ""
echo "Setting up environment..."
echo ""

# Create .env if it doesn't exist
if [ ! -f ".env" ]; then
    echo "Creating .env from .env.example..."
    cp .env.example .env
    echo -e "${YELLOW}Please edit .env with your API keys and configuration${NC}"
else
    echo -e "${GREEN}✓${NC} .env already exists"
fi

echo ""
echo "Running tests to verify setup..."
echo ""

# Run tests
source venv/bin/activate
pytest tests/ -q --tb=no

echo ""
echo "==================================="
echo -e "${GREEN}Setup complete!${NC}"
echo "==================================="
echo ""
echo "Next steps:"
echo ""
echo "1. Edit .env with your API keys:"
echo "   - ANTHROPIC_API_KEY (required for AI features)"
echo "   - GITHUB_TOKEN (required for PR creation)"
echo "   - GCP_PROJECT_ID (required for Cloud Logging)"
echo "   - PAGERDUTY_ROUTING_KEY (optional)"
echo ""
echo "2. Start the backend:"
echo "   source venv/bin/activate"
echo "   uvicorn backend.main:app --reload --port 8000"
echo ""
echo "3. Start the frontend (in another terminal):"
echo "   cd frontend && npm run dev"
echo ""
echo "4. Or use Docker Compose:"
echo "   docker-compose up"
echo ""
echo "Dashboard: http://localhost:3000"
echo "API: http://localhost:8000"
echo "API Docs: http://localhost:8000/docs"
echo ""
