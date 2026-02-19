def calcular_credito(monto, tasa_interes, plazo):
    """
    Calcula la cuota mensual de un crédito.
    :param monto: Monto total del crédito
    :param tasa_interes: Tasa de interés anual en porcentaje
    :param plazo: Plazo en años
    :return: Cuota mensual
    """
    tasa_mensual = (tasa_interes / 100) / 12
    numero_cuotas = plazo * 12
    cuota = monto * (tasa_mensual * (1 + tasa_mensual) ** numero_cuotas) / ((1 + tasa_mensual) ** numero_cuotas - 1)
    return cuota


if __name__ == '__main__':
    # Ejemplo de uso
    monto = 100000  # Monto del crédito
    tasa_interes = 5  # Tasa de interés anual
    plazo = 15  # Plazo en años
    cuota = calcular_credito(monto, tasa_interes, plazo)
    print(f'La cuota mensual es: {cuota:.2f}')
