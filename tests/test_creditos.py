from django.test import TestCase
from .models import Credito

class CreditoModelTest(TestCase):
    def setUp(self):
        Credito.objects.create(monto=10000.00, tasa_interes=5.0, plazo_meses=24)

    def test_credito_str(self):
        credito = Credito.objects.get(id=1)
        self.assertEqual(str(credito), 'Credito 1: 10000.00 a 5.0% por 24 meses')
