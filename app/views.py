from django.shortcuts import render
from .forms import CreditCalculatorForm


def credit_calculator(request):
    if request.method == 'POST':
        form = CreditCalculatorForm(request.POST)
        if form.is_valid():
            amount = form.cleaned_data['amount']
            interest_rate = form.cleaned_data['interest_rate']
            term = form.cleaned_data['term']
            # Lógica para calcular el pago mensual
            monthly_interest_rate = interest_rate / 100 / 12
            number_of_payments = term * 12
            monthly_payment = (amount * monthly_interest_rate) / (1 - (1 + monthly_interest_rate) ** -number_of_payments)
            return render(request, 'calculator.html', {'form': form, 'monthly_payment': monthly_payment})
    else:
        form = CreditCalculatorForm()
    return render(request, 'calculator.html', {'form': form})
