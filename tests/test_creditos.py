import pytest
from app.models import Credito
from rest_framework.test import APIClient
from django.urls import reverse

@pytest.mark.django_db
class TestCredito:
    def setup_method(self):
        self.client = APIClient()

    def test_crear_credito(self):
        response = self.client.post(reverse('credito-list'), {
            'monto': 10000.00,
            'tasa_interes': 5.0,
            'plazo_meses': 12
        })
        assert response.status_code == 201
        assert Credito.objects.count() == 1
        assert Credito.objects.get().monto == 10000.00
