def calculate_credit(principal, rate, time):
    """Calcula el monto total a pagar después de un período dado."""
    return principal * (1 + rate * time)

if __name__ == '__main__':
    # Ejemplo de uso
    print(calculate_credit(1000, 0.05, 2))  # Salida esperada: 1100.0
    print(calculate_credit(2000, 0.03, 1))  # Salida esperada: 2060.0
    print(calculate_credit(1500, 0.04, 3))  # Salida esperada: 1860.0
