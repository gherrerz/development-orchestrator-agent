import pytest
from fastapi.testclient import TestClient
from app.main import app

client = TestClient(app)

def test_simulate_credit_endpoint_success():
    payload = {
        "principal": 1000,
        "annual_rate": 12,
        "years": 1
    }
    response = client.post("/simulate-credit", json=payload)
    assert response.status_code == 200
    data = response.json()
    assert "monthly_payment" in data
    assert "total_payment" in data
    assert "total_interest" in data

def test_simulate_credit_endpoint_invalid_payload():
    payload = {
        "principal": -1000,
        "annual_rate": 12,
        "years": 1
    }
    response = client.post("/simulate-credit", json=payload)
    assert response.status_code == 422
