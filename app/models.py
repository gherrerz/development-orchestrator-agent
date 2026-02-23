from django.db import models

class Credito(models.Model):
    monto = models.DecimalField(max_digits=10, decimal_places=2)
    tasa_interes = models.DecimalField(max_digits=5, decimal_places=2)
    plazo_meses = models.IntegerField()
    fecha_creacion = models.DateField(auto_now_add=True)

    def __str__(self):
        return f'Credito {self.id}: {self.monto} a {self.tasa_interes}% por {self.plazo_meses} meses'
