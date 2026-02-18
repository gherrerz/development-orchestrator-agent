class CreditCalculator:
    def __init__(self, principal, rate, time):
        self.principal = principal
        self.rate = rate
        self.time = time

    def calculate_total_payment(self):
        return self.principal * (1 + self.rate * self.time)

    def calculate_monthly_payment(self):
        total_payment = self.calculate_total_payment()
        return total_payment / (self.time * 12)

    def calculate_interest(self):
        return self.calculate_total_payment() - self.principal
