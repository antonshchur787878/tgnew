from django.db.models.signals import post_save, post_delete
from django.dispatch import receiver
import logging
from .models import Bot, BotSettings, BotPosition
from .tasks import log_action

logger = logging.getLogger(__name__)

@receiver(post_save, sender=Bot)
def create_bot_settings(sender, instance, created, **kwargs):
    """
    Создаёт настройки бота после его создания.
    """
    if created:
        logger.info(f"Создание настроек для нового бота ID {instance.id}")
        BotSettings.objects.get_or_create(
            bot=instance,
            defaults={
                'signal_type': 'rsi',
                'signal_params': {'period': 14, 'threshold': 30},
                'signal_interval': '1h',
                'take_profit': 1.0,
                'grid_overlap': 10.0,
                'grid_orders': 5,
                'martingale': 2.0,
                'grid_spacing': 0.5
            }
        )
        BotPosition.objects.get_or_create(bot=instance)
        log_action.delay(
            user_id=instance.user.id,
            bot_id=instance.id,
            action="Bot settings created",
            details=f"Default settings created for bot {instance.name}",
            status="success"
        )

@receiver(post_delete, sender=Bot)
def delete_bot_related_objects(sender, instance, **kwargs):
    """
    Удаляет связанные объекты после удаления бота.
    """
    logger.info(f"Удаление связанных объектов для бота ID {instance.id}")
    BotSettings.objects.filter(bot=instance).delete()
    BotPosition.objects.filter(bot=instance).delete()
    log_action.delay(
        user_id=instance.user.id,
        bot_id=instance.id,
        action="Related objects deleted",
        details=f"Settings and position deleted for bot {instance.name}",
        status="success"
    )