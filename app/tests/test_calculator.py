import pytest
from django.urls import reverse
from .forms import CreditCalculatorForm


@pytest.mark.django_db
def test_credit_calculator(client):
    response = client.get(reverse('credit_calculator'))
    assert response.status_code == 200
    assert 'form' in response.context

    # Simular envío de formulario
    response = client.post(reverse('credit_calculator'), {
        'amount': 10000,
        'interest_rate': 5,
        'term': 2
    })
    assert response.status_code == 200
    assert 'total_payment' in response.context
    # Cálculo esperado: 10000 * (1 + 0.05) ** 2 = 11025.0
    assert response.context['total_payment'] == 11025.0
