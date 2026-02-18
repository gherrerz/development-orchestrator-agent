from django.shortcuts import render
from .calculadora import calcular_credito

def calcular(request):
    if request.method == 'POST':
        monto = float(request.POST.get('monto'))
        tasa_interes = float(request.POST.get('tasa_interes'))
        plazo = int(request.POST.get('plazo'))
        cuota = calcular_credito(monto, tasa_interes, plazo)
        return render(request, 'resultado.html', {'cuota': cuota})
    return render(request, 'calcular.html')
