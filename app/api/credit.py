from fastapi import APIRouter, HTTPException
from app.schemas.credit_simulation import CreditSimulationRequest, CreditSimulationResponse
from app.services.credit_simulation import simulate_credit

router = APIRouter()

@router.post("/simulate-credit", response_model=CreditSimulationResponse)
async def simulate_credit_endpoint(request: CreditSimulationRequest):
    result = simulate_credit(request.amount, request.term, request.interest_rate)
    if not result:
        raise HTTPException(status_code=400, detail="Parámetros inválidos para la simulación")
    return result
