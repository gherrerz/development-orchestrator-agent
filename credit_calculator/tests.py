import pytest
from django.urls import reverse
from .models import calcular_cuota

@pytest.mark.parametrize("monto, tasa, plazo, expected", [
    (10000, 12, 12, 888.49),
    (5000, 0, 10, 500.0),
    (12000, 10, 24, 552.5),
])
def test_calcular_cuota(monto, tasa, plazo, expected):
    cuota = calcular_cuota(monto, tasa, plazo)
    assert round(cuota, 2) == expected

def test_calcular_cuota_invalid():
    import pytest
    with pytest.raises(ValueError):
        calcular_cuota(0, 10, 12)
    with pytest.raises(ValueError):
        calcular_cuota(1000, -1, 12)
    with pytest.raises(ValueError):
        calcular_cuota(1000, 10, 0)

@pytest.mark.django_db
def test_view_get(client):
    url = reverse("credit_calculator:index")
    response = client.get(url)
    assert response.status_code == 200
    assert b"Simulador de Créditos" in response.content

@pytest.mark.django_db
def test_view_post_valid(client):
    url = reverse("credit_calculator:index")
    data = {"monto": "10000", "tasa": "12", "plazo": "12"}
    response = client.post(url, data)
    assert response.status_code == 200
    assert b"La cuota mensual es" in response.content

@pytest.mark.django_db
def test_view_post_invalid(client):
    url = reverse("credit_calculator:index")
    data = {"monto": "-100", "tasa": "12", "plazo": "12"}
    response = client.post(url, data)
    assert response.status_code == 200
    assert b"Error en los datos ingresados" in response.content
