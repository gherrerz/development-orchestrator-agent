## Credit Simulation Endpoint

Se ha añadido un endpoint POST `/credit-simulation` para simular créditos.

### Uso

Enviar un JSON con los siguientes campos:

```json
{
  "amount": 10000.0,  
  "term": 12,          
  "interest_rate": 12.0
}
```

El endpoint devuelve un JSON con:

```json
{
  "monthly_payment": 888.49,
  "total_payment": 10661.88
}
```
