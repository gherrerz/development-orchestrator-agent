from django.shortcuts import render
from .calculadora import calcular_credito, calcular_total_a_pagar


def simular_credito(request):
    if request.method == 'POST':
        monto = float(request.POST.get('monto'))
        tasa_interes = float(request.POST.get('tasa_interes'))
        plazo = int(request.POST.get('plazo'))
        cuota = calcular_credito(monto, tasa_interes, plazo)
        total = calcular_total_a_pagar(cuota, plazo)
        return render(request, 'resultado.html', {'cuota': cuota, 'total': total})
    return render(request, 'simulador.html')
