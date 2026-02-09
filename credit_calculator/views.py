from django.shortcuts import render
from django.views.decorators.http import require_http_methods
from .models import calcular_cuota

@require_http_methods(["GET", "POST"])
def calculadora_creditos(request):
    context = {}
    if request.method == "POST":
        try:
            monto = float(request.POST.get("monto", "0"))
            tasa = float(request.POST.get("tasa", "0"))
            plazo = int(request.POST.get("plazo", "0"))
            cuota = calcular_cuota(monto, tasa, plazo)
            context["resultado"] = f"La cuota mensual es: {cuota:.2f}"
        except (ValueError, TypeError) as e:
            context["error"] = f"Error en los datos ingresados: {str(e)}"
    return render(request, "credit_calculator/index.html", context)
