import pytest
from django.urls import reverse


@pytest.mark.django_db
def test_credit_calculator(client):
    response = client.post(reverse('credit_calculator'), {
        'amount': 10000,
        'interest_rate': 5,
        'term': 2
    })
    assert response.status_code == 200
    assert 'Pago mensual' in response.content.decode()
