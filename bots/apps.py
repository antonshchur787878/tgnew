from django.apps import AppConfig


class BotsConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'bots'

    def ready(self):
        import bots.signals  # Подключаем сигналы, если они есть