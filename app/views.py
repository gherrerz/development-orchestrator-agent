from django.shortcuts import render
from .calculator import calcular_credito


def calcular_credito_view(request):
    if request.method == 'POST':
        monto = float(request.POST.get('monto'))
        tasa_interes = float(request.POST.get('tasa_interes'))
        plazo = int(request.POST.get('plazo'))
        pago_mensual = calcular_credito(monto, tasa_interes, plazo)
        return render(request, 'resultado.html', {'pago_mensual': pago_mensual})
    return render(request, 'calculo.html')
