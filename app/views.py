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
            monthly_payment = (amount * interest_rate / 100 / 12) / (1 - (1 + interest_rate / 100 / 12) ** -term)
            return render(request, 'calculator.html', {'form': form, 'monthly_payment': monthly_payment})
    else:
        form = CreditCalculatorForm()
    return render(request, 'calculator.html', {'form': form})
