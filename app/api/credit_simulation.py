from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from app.credit_calculator import simulate_credit

router = APIRouter()

class CreditSimulationRequest(BaseModel):
    amount: float = Field(..., gt=0, description="Monto del crédito")
    term: int = Field(..., gt=0, description="Plazo en meses")
    interest_rate: float = Field(..., ge=0, description="Tasa de interés anual en porcentaje")

class CreditSimulationResponse(BaseModel):
    monthly_payment: float
    total_payment: float

@router.post("/credit-simulation", response_model=CreditSimulationResponse)
async def credit_simulation(data: CreditSimulationRequest):
    try:
        result = simulate_credit(data.amount, data.term, data.interest_rate)
        return result
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
