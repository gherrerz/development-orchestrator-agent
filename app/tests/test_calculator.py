import pytest
from django.urls import reverse
from .forms import CreditCalculatorForm


@pytest.mark.django_db
def test_credit_calculator(client):
    response = client.post(reverse('credit_calculator'), {
        'amount': 10000,
        'interest_rate': 5,
        'term': 24
    })
    assert response.status_code == 200
    assert b'Pago mensual:' in response.content


def test_credit_calculator_form():
    form_data = {
        'amount': 10000,
        'interest_rate': 5,
        'term': 24
    }
    form = CreditCalculatorForm(data=form_data)
    assert form.is_valid()
    assert form.cleaned_data['amount'] == 10000
    assert form.cleaned_data['interest_rate'] == 5
    assert form.cleaned_data['term'] == 24
