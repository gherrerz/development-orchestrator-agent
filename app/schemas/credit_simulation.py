from pydantic import BaseModel, Field, conint, confloat

class CreditSimulationRequest(BaseModel):
    amount: confloat(gt=0) = Field(..., description="Monto del crédito")
    term: conint(gt=0) = Field(..., description="Plazo en meses")
    interest_rate: confloat(ge=0) = Field(..., description="Tasa de interés anual en porcentaje")

class CreditSimulationResponse(BaseModel):
    total_payment: float = Field(..., description="Monto total a pagar")
    monthly_payment: float = Field(..., description="Cuota mensual")
