from django.shortcuts import render
from .calculator import CreditCalculator


def calculate_credit(request):
    if request.method == 'POST':
        principal = float(request.POST.get('principal'))
        rate = float(request.POST.get('rate'))
        time = float(request.POST.get('time'))
        calculator = CreditCalculator(principal, rate, time)
        total_payment = calculator.calculate_total_payment()
        monthly_payment = calculator.calculate_monthly_payment()
        interest = calculator.calculate_interest()
        return render(request, 'result.html', {'total_payment': total_payment, 'monthly_payment': monthly_payment, 'interest': interest})
    return render(request, 'calculator.html')
