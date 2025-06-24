# bots/tasks.py
from celery import shared_task
from celery.exceptions import SoftTimeLimitExceeded
from django.core.cache import cache
import time
import logging
import sentry_sdk
from .models import Bot, LogEntry
from .strategies import TradingStrategy, stop_bot

logger = logging.getLogger(__name__)

@shared_task
def log_action(user_id, bot_id, action, details, status, error_message=None, financial_result=None):
    """
    Логирует действие бота.

    Args:
        user_id (int): ID пользователя.
        bot_id (int): ID бота.
        action (str): Действие.
        details (str): Подробности.
        status (str): Статус действия.
        error_message (str, optional): Сообщение об ошибке.
        financial_result (dict, optional): Финансовые результаты.
    """
    log_data = {
        "user_id": user_id,
        "bot_id": bot_id,
        "action": action,
        "details": details,
        "status": status,
        "error_message": error_message,
        "financial_result": financial_result
    }
    logger.info(f"Логирование действия: {log_data}")
    try:
        if not user_id:
            logger.warning("user_id не указан, пропуск логирования")
            return
        if not bot_id:
            logger.warning("bot_id не указан, пропуск логирования")
            return
        bot = Bot.objects.get(id=bot_id)
        LogEntry.objects.create(
            user_id=user_id,
            bot=bot,
            action=action,
            details=details,
            status=status,
            error_message=error_message,
            financial_result=financial_result
        )
        logger.info(f"Действие успешно записано в лог: {action}")
    except Bot.DoesNotExist:
        logger.error(f"Бот с id={bot_id} не найден при логировании")
        sentry_sdk.capture_exception(Bot.DoesNotExist())
    except Exception as e:
        logger.error(f"Ошибка при логировании действия: {str(e)}", exc_info=True)
        sentry_sdk.capture_exception(e)

@shared_task(bind=True, max_retries=3, soft_time_limit=50, time_limit=60)
def run_trading_strategy(self, bot_id):
    """
    Выполняет торговую стратегию для бота.

    Args:
        bot_id (int): ID бота.
    """
    start_time = time.time()
    task_key = f"run_trading_strategy_{bot_id}"
    logger.info(f"Запуск торговой стратегии для бота {bot_id}")
    bot = None
    try:
        # Проверяем блокировку задачи
        if cache.get(task_key):
            logger.warning(f"Задача для бота {bot_id} уже выполняется, пропуск")
            return

        # Устанавливаем блокировку
        cache.set(task_key, True, timeout=60)

        bot = Bot.objects.get(id=bot_id)
        if not bot.is_running or bot.status != 'active':
            logger.warning(f"Бот {bot_id} не активен или не запущен, пропуск задачи")
            cache.delete(task_key)
            return

        # Добавляем контекст для Sentry
        with sentry_sdk.configure_scope() as scope:
            scope.set_tag("bot_id", bot_id)
            scope.set_context("bot", {
                "exchange": bot.api_key.exchange,
                "trading_pair": bot.trading_pair,
                "strategy": bot.strategy,
                "trade_mode": bot.trade_mode,
                "signal_type": bot.settings.signal_type,
                "signal_interval": bot.settings.signal_interval
            })

        strategy = TradingStrategy(bot)
        logger.debug(f"Стратегия инициализирована для бота {bot_id}: exchange={bot.api_key.exchange}, "
                     f"trading_pair={bot.trading_pair}, category={'futures' if bot.strategy == 'futures' else 'spot'}")

        # Проверяем сигнал
        signal = strategy.check_signal()
        logger.debug(f"Результат проверки сигнала для бота {bot_id}: {signal}")

        # Выполняем стратегию
        if signal:
            logger.info(f"Сигнал сработал для бота {bot_id}, выполнение стратегии")
            strategy.execute()
        else:
            logger.info(f"Сигнал не сработал для бота {bot_id}, ожидание следующей проверки")

        # Логируем результат
        position_obj = strategy.position_obj
        financial_result = {
            'position': position_obj.position,
            'avg_price': position_obj.avg_price,
            'deals_completed': bot.deals_completed
        }
        log_action.delay(
            user_id=bot.user.id if bot.user else None,
            bot_id=bot.id,
            action="Strategy executed",
            details=f"Выполнена стратегия {bot.trade_mode} для бота {bot.name}, "
                    f"сигналы: {strategy.combined_signals or strategy.signal_type}, "
                    f"позиция: {position_obj.position}, сделок завершено: {bot.deals_completed}",
            status="success",
            financial_result=financial_result
        )

        # Проверяем условие остановки после сделок
        if bot.settings.stop_after_deals and bot.deals_completed >= bot.settings.stop_after_deals:
            logger.info(f"Бот {bot_id} остановлен после достижения лимита сделок: {bot.deals_completed}")
            stop_bot.delay(bot_id)
            cache.delete(task_key)
            return

        # Планируем следующую задачу, используя task_interval
        interval_seconds = bot.settings.task_interval * 60  # Используем новое поле
        if bot.is_running and bot.status == 'active':
            run_trading_strategy.apply_async(
                (bot_id,),
                countdown=interval_seconds,
                task_id=f"run_trading_strategy_{bot_id}_{int(time.time())}"
            )
            logger.debug(f"Запланирована следующая задача для бота {bot_id} через {interval_seconds} секунд")

        execution_time = time.time() - start_time
        logger.info(f"Задача завершена за {execution_time:.2f} секунд")
        if execution_time > 50:
            logger.warning(f"Выполнение задачи для бота {bot_id} заняло слишком много времени: {execution_time:.2f} секунд")

    except SoftTimeLimitExceeded:
        logger.error(f"Превышен мягкий лимит времени выполнения для бота {bot_id}")
        user_id = bot.user.id if bot and bot.user else None
        log_action.delay(
            user_id=user_id,
            bot_id=bot_id,
            action="Strategy execution failed",
            details="Превышен мягкий лимит времени выполнения",
            status="error",
            error_message="SoftTimeLimitExceeded"
        )
        sentry_sdk.capture_exception(SoftTimeLimitExceeded())
    except Bot.DoesNotExist:
        logger.error(f"Бот с id={bot_id} не найден")
        sentry_sdk.capture_exception(Bot.DoesNotExist())
    except Exception as e:
        user_id = bot.user.id if bot and bot.user else None
        logger.error(
            f"Ошибка в торговой стратегии для бота {bot_id}: {str(e)} "
            f"(Попытка {self.request.retries + 1}/{self.max_retries})",
            exc_info=True
        )
        log_action.delay(
            user_id=user_id,
            bot_id=bot_id,
            action="Strategy execution failed",
            details=f"Ошибка выполнения стратегии: {str(e)}",
            status="error",
            error_message=str(e)
        )
        sentry_sdk.capture_exception(e)
        # Экспоненциальная задержка для повторных попыток
        countdown = 60 * (2 ** self.request.retries)
        raise self.retry(countdown=countdown, exc=e)
    finally:
        # Удаляем блокировку после завершения задачи
        cache.delete(task_key)