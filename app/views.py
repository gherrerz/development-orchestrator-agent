from django.shortcuts import render
from .forms import CreditCalculatorForm


def credit_calculator(request):
    if request.method == 'POST':
        form = CreditCalculatorForm(request.POST)
        if form.is_valid():
            amount = form.cleaned_data['amount']
            interest_rate = form.cleaned_data['interest_rate']
            years = form.cleaned_data['years']
            total_payment = amount * (1 + interest_rate / 100) ** years
            return render(request, 'calculator.html', {'form': form, 'total_payment': total_payment})
    else:
        form = CreditCalculatorForm()
    return render(request, 'calculator.html', {'form': form})
