from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field
from app.credit_calculator import simulate_credit

app = FastAPI()

class CreditSimulationRequest(BaseModel):
    principal: float = Field(..., gt=0, description="Monto del crédito")
    annual_rate: float = Field(..., ge=0, description="Tasa de interés anual en porcentaje")
    years: int = Field(..., gt=0, description="Plazo en años")

class CreditSimulationResponse(BaseModel):
    monthly_payment: float
    total_payment: float
    total_interest: float

@app.post("/simulate-credit", response_model=CreditSimulationResponse)
def simulate_credit_endpoint(request: CreditSimulationRequest):
    try:
        result = simulate_credit(request.principal, request.annual_rate, request.years)
        return result
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))
