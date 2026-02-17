from django import forms


class CreditCalculatorForm(forms.Form):
    amount = forms.DecimalField(label='Monto del crédito', max_digits=10, decimal_places=2)
    interest_rate = forms.DecimalField(label='Tasa de interés (%)', max_digits=5, decimal_places=2)
    term = forms.IntegerField(label='Plazo (meses)')
