from rest_framework import viewsets
from .models import Credito
from .serializers import CreditoSerializer

class CreditoViewSet(viewsets.ModelViewSet):
    queryset = Credito.objects.all()
    serializer_class = CreditoSerializer
