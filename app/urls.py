from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import CreditoViewSet

router = DefaultRouter()
router.register(r'creditos', CreditoViewSet)

urlpatterns = [
    path('', include(router.urls)),
]
