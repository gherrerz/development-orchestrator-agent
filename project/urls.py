from django.urls import include, path

urlpatterns = [
    # otras urls...
    path('credit_calculator/', include('credit_calculator.urls', namespace='credit_calculator')),
]
