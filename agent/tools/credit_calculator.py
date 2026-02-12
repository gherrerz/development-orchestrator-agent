def calculate_credit(principal, rate, time):
    """Calcula el monto total a pagar en un crédito."""
    return principal * (1 + rate) ** time

if __name__ == '__main__':
    # Ejemplo de uso
    print(calculate_credit(1000, 0.05, 2))  # Salida esperada: 1102.5
