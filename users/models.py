from django.contrib.auth.models import AbstractUser
from django.db import models


class CustomUser(AbstractUser):
    """
    Кастомная модель пользователя.
    """
    referral_code = models.CharField(max_length=64, unique=True, null=True, blank=True)  # Реферальный код
    balance = models.DecimalField(max_digits=10, decimal_places=2, default=0.00)  # Баланс пользователя
    telegram_id = models.CharField(max_length=255, null=True, blank=True)  # Идентификатор Telegram

    def __str__(self):
        return self.username