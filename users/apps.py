from django.apps import AppConfig

class UsersConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'users'

    def ready(self):
        # Регистрация кастомного провайдера после полной инициализации приложений
        from django.conf import settings
        if settings.INSTALLED_APPS and 'allauth.socialaccount' in settings.INSTALLED_APPS:
            from allauth.socialaccount.providers import registry
            from .providers import TelegramProvider
            registry.register(TelegramProvider)  # Регистрируем без проверки