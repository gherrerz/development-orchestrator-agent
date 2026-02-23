import pytest
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient
from .models import Credito

@pytest.mark.django_db
class TestCreditoAPI:
    def setup_method(self):
        self.client = APIClient()

    def test_list_creditos(self):
        response = self.client.get(reverse('credito-list'))
        assert response.status_code == status.HTTP_200_OK

    def test_create_credito(self):
        data = {
            'monto': 10000.00,
            'tasa_interes': 5.0,
            'plazo_meses': 12,
            'fecha_inicio': '2023-01-01'
        }
        response = self.client.post(reverse('credito-list'), data)
        assert response.status_code == status.HTTP_201_CREATED
        assert Credito.objects.count() == 1
        assert Credito.objects.get().monto == 10000.00
