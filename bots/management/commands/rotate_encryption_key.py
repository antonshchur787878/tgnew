from django.core.management.base import BaseCommand
from cryptography.fernet import Fernet
from bots.models import APIKey
import logging

logger = logging.getLogger(__name__)

class Command(BaseCommand):
    help = 'Ротирует ключ шифрования для всех API-ключей'

    def handle(self, *args, **options):
        new_key = Fernet.generate_key()  # Генерируем новый ключ
        api_keys = APIKey.objects.all()
        total = api_keys.count()
        success = 0

        self.stdout.write(f"Начало ротации ключей для {total} API-ключей...")

        for api_key in api_keys:
            try:
                api_key.rotate_encryption_key(new_key)
                success += 1
                self.stdout.write(f"Успешно ротирован ключ для API-ключа ID {api_key.id}")
            except Exception as e:
                logger.error(f"Ошибка ротации ключа для API-ключа ID {api_key.id}: {str(e)}")
                self.stdout.write(f"Ошибка для API-ключа ID {api_key.id}: {str(e)}", self.style.ERROR)

        self.stdout.write(f"Ротация завершена: {success}/{total} ключей успешно обновлено", self.style.SUCCESS)
        self.stdout.write(f"Новый ключ шифрования: {new_key.decode()}", self.style.WARNING)
        self.stdout.write("Обновите ENCRYPTION_KEY в настройках окружения вручную!")