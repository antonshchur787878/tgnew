import os
from celery import Celery

# Устанавливаем модуль настроек Django
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'core.settings')

# Создаём экземпляр Celery
app = Celery('core')

# Загружаем настройки из settings.py с префиксом CELERY
app.config_from_object('django.conf:settings', namespace='CELERY')

# Автоматически обнаруживаем задачи в приложениях
app.autodiscover_tasks()