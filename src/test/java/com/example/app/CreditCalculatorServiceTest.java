package com.example.app;

import static org.junit.jupiter.api.Assertions.assertEquals;
import org.junit.jupiter.api.Test;

class CreditCalculatorServiceTest {

    private final CreditCalculatorService calculator = new CreditCalculatorService();

    @Test
    void testCalculateCredit() {
        // Test con valores de ejemplo
        double amount = 1000;
        double interestRate = 0.05; // 5%
        int years = 10;
        double expected = 1628.894626777442; // Cálculo: 1000 * (1 + 0.05)^10
        assertEquals(expected, calculator.calculateCredit(amount, interestRate, years), 0.01);
    }
}
