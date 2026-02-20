package com.example.app;

import org.springframework.stereotype.Service;

@Service
public class CreditCalculatorService {

    public double calculateCredit(double amount, double interestRate, int years) {
        // Fórmula simple para calcular el crédito
        return amount * Math.pow(1 + interestRate, years);
    }
}
