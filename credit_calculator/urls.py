from django.urls import path
from . import views

app_name = "credit_calculator"

urlpatterns = [
    path("", views.calculadora_creditos, name="index"),
]
