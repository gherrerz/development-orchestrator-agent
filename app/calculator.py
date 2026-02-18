class CreditCalculator:
    def __init__(self, principal, rate, time):
        self.principal = principal
        self.rate = rate
        self.time = time

    def calculate_interest(self):
        # Cálculo del interés simple
        return self.principal * (self.rate / 100) * self.time

    def calculate_total_payment(self):
        # Cálculo del pago total
        interest = self.calculate_interest()
        return self.principal + interest
