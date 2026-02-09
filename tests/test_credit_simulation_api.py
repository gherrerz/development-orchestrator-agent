import pytest
from fastapi.testclient import TestClient
from fastapi import FastAPI
from app.api import credit_simulation

app = FastAPI()
app.include_router(credit_simulation.router)

client = TestClient(app)

def test_credit_simulation_success():
    payload = {"amount": 10000, "term": 12, "interest_rate": 12}
    response = client.post("/credit-simulation", json=payload)
    assert response.status_code == 200
    data = response.json()
    assert "monthly_payment" in data
    assert "total_payment" in data
    assert data["monthly_payment"] > 0
    assert data["total_payment"] > 0

def test_credit_simulation_invalid_params():
    payload = {"amount": -10000, "term": 12, "interest_rate": 12}
    response = client.post("/credit-simulation", json=payload)
    assert response.status_code == 422  # validation error from Pydantic

    payload = {"amount": 10000, "term": 0, "interest_rate": 12}
    response = client.post("/credit-simulation", json=payload)
    assert response.status_code == 422

    payload = {"amount": 10000, "term": 12, "interest_rate": -1}
    response = client.post("/credit-simulation", json=payload)
    assert response.status_code == 422
