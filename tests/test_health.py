"""
Tests for health and info endpoints.

Verifies the FastAPI application starts correctly and health checks work.
"""

import pytest
from fastapi.testclient import TestClient

from backend.main import app


@pytest.fixture
def client():
    """Create a test client."""
    return TestClient(app)


class TestHealthEndpoints:
    """Tests for health check endpoints."""

    def test_health(self, client):
        """Test basic health endpoint."""
        response = client.get("/health")

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "healthy"
        assert "timestamp" in data

    def test_live(self, client):
        """Test liveness endpoint."""
        response = client.get("/health/live")

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "alive"

    def test_ready(self, client):
        """Test readiness endpoint."""
        response = client.get("/health/ready")

        # May return 200 or 503 depending on repo paths
        assert response.status_code in [200, 503]
        data = response.json()
        assert "status" in data


class TestInfoEndpoint:
    """Tests for info endpoint."""

    def test_info(self, client):
        """Test info endpoint."""
        response = client.get("/info")

        assert response.status_code == 200
        data = response.json()
        assert data["name"] == "On Call Helper"
        assert "version" in data
        assert "repositories" in data


class TestMetricsEndpoint:
    """Tests for metrics endpoint."""

    def test_metrics(self, client):
        """Test metrics endpoint."""
        response = client.get("/metrics")

        assert response.status_code == 200
        data = response.json()
        assert "total_incidents" in data
        assert "auto_fixed" in data
        assert "escalated" in data


class TestIncidentEndpoints:
    """Tests for incident endpoints."""

    def test_list_incidents_empty(self, client):
        """Test listing incidents when empty."""
        response = client.get("/incidents")

        assert response.status_code == 200
        data = response.json()
        assert "incidents" in data
        assert "count" in data

    def test_list_incidents_invalid_status(self, client):
        """Test listing incidents with invalid status filter."""
        response = client.get("/incidents?status=invalid")

        assert response.status_code == 400
        data = response.json()
        assert "error" in data

    def test_get_incident_not_found(self, client):
        """Test getting a non-existent incident."""
        response = client.get("/incidents/OCH-NOTFOUND")

        assert response.status_code == 404
        data = response.json()
        assert "error" in data
