import pytest
from fastapi.testclient import TestClient
from app.main import app
from app.schemas.credit_simulation import CreditSimulationRequest
from app.services.credit_simulation import simulate_credit

client = TestClient(app)

def test_credit_simulation_request_validation():
    # Valid data
    data = {"amount": 1000, "term": 12, "interest_rate": 5}
    req = CreditSimulationRequest(**data)
    assert req.amount == 1000
    assert req.term == 12
    assert req.interest_rate == 5

    # Invalid data
    with pytest.raises(Exception):
        CreditSimulationRequest(amount=-1000, term=12, interest_rate=5)
    with pytest.raises(Exception):
        CreditSimulationRequest(amount=1000, term=0, interest_rate=5)
    with pytest.raises(Exception):
        CreditSimulationRequest(amount=1000, term=12, interest_rate=-1)

def test_simulate_credit():
    result = simulate_credit(1000, 12, 5)
    assert result["total_payment"] == 1600.0
    assert result["monthly_payment"] == 133.33

    # Invalid inputs
    assert simulate_credit(-1000, 12, 5) == {}
    assert simulate_credit(1000, 0, 5) == {}
    assert simulate_credit(1000, 12, -1) == {}

def test_simulate_credit_endpoint():
    response = client.post("/credit/simulate-credit", json={"amount": 1000, "term": 12, "interest_rate": 5})
    assert response.status_code == 200
    data = response.json()
    assert data["total_payment"] == 1600.0
    assert data["monthly_payment"] == 133.33

    # Test invalid input
    response = client.post("/credit/simulate-credit", json={"amount": -1000, "term": 12, "interest_rate": 5})
    assert response.status_code == 400
    assert response.json()["detail"] == "Parámetros inválidos para la simulación"
