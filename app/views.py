from django.shortcuts import render
from .forms import CreditCalculatorForm


def credit_calculator(request):
    if request.method == 'POST':
        form = CreditCalculatorForm(request.POST)
        if form.is_valid():
            # Lógica de cálculo de créditos
            amount = form.cleaned_data['amount']
            interest_rate = form.cleaned_data['interest_rate']
            term = form.cleaned_data['term']
            total_payment = amount * (1 + interest_rate / 100) ** term
            return render(request, 'calculator.html', {'form': form, 'total_payment': total_payment})
    else:
        form = CreditCalculatorForm()
    return render(request, 'calculator.html', {'form': form})
