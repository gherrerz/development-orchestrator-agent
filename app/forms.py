from django import forms


class CreditCalculatorForm(forms.Form):
    amount = forms.FloatField(label='Monto del crédito', min_value=0)
    interest_rate = forms.FloatField(label='Tasa de interés (%)', min_value=0)
    years = forms.IntegerField(label='Años', min_value=1)
