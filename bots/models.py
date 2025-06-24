# bots/models.py
from django.db import models
from django.conf import settings
from cryptography.fernet import Fernet
from django.utils import timezone
from django.core.validators import MinValueValidator, MaxValueValidator
from django.core.exceptions import ValidationError
import logging

logger = logging.getLogger(__name__)

class APIKey(models.Model):
    BYBIT = 'bybit'
    BINANCE = 'binance'
    OKX = 'okx'
    EXCHANGE_CHOICES = [
        (BYBIT, 'Bybit'),
        (BINANCE, 'Binance'),
        (OKX, 'OKX'),
    ]

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='api_keys',
        verbose_name="Пользователь"
    )
    exchange = models.CharField(
        max_length=20,
        choices=EXCHANGE_CHOICES,
        verbose_name="Биржа"
    )
    api_key = models.CharField(max_length=1024, verbose_name="API-ключ")
    api_secret = models.CharField(max_length=1024, verbose_name="Секретный ключ")
    passphrase = models.CharField(
        max_length=1024,
        verbose_name="Пароль API (для OKX)",
        blank=True,
        null=True,
        help_text="Требуется только для OKX"
    )
    added_at = models.DateTimeField(auto_now_add=True, verbose_name="Дата добавления")
    encryption_version = models.CharField(max_length=10, default='v1', verbose_name="Версия шифрования")

    class Meta:
        unique_together = ('user', 'api_key')
        verbose_name = "API-ключ"
        verbose_name_plural = "API-ключи"
        indexes = [
            models.Index(fields=['user', 'exchange']),
        ]

    def save(self, *args, **kwargs):
        cipher = Fernet(settings.ENCRYPTION_KEY)
        for field in ('api_key', 'api_secret', 'passphrase'):
            value = getattr(self, field)
            if field != 'passphrase' and not value:
                logger.error(f"{field} пустой для пользователя {self.user.username}")
                raise ValueError(f"{field} не может быть пустым.")
            if value and not value.startswith('enc:'):
                try:
                    logger.info(f"Шифрование {field} для {self.user.username}")
                    setattr(self, field, 'enc:' + cipher.encrypt(value.encode()).decode())
                except Exception as e:
                    logger.error(f"Ошибка шифрования {field} для {self.user.username}: {str(e)}")
                    raise ValidationError(f"Ошибка шифрования {field}: {str(e)}")
        super().save(*args, **kwargs)

    def get_decrypted_keys(self):
        try:
            cipher = Fernet(settings.ENCRYPTION_KEY)
            decrypted_api_key = cipher.decrypt(self.api_key.replace('enc:', '').encode()).decode()
            decrypted_api_secret = cipher.decrypt(self.api_secret.replace('enc:', '').encode()).decode()
            decrypted_passphrase = cipher.decrypt(self.passphrase.replace('enc:', '').encode()).decode() if self.passphrase else ""
            logger.debug(f"API-ключи успешно дешифрованы для {self.user.username}")
            return {
                "api_key": decrypted_api_key,
                "api_secret": decrypted_api_secret,
                "passphrase": decrypted_passphrase
            }
        except Exception as e:
            logger.error(f"Не удалось расшифровать API-ключи для {self.user.username}: {str(e)}")
            raise ValueError(f"Не удалось расшифровать API-ключи: {str(e)}")

    def rotate_encryption_key(self, new_key):
        try:
            old_cipher = Fernet(settings.ENCRYPTION_KEY)
            new_cipher = Fernet(new_key)
            decrypted_api_key = old_cipher.decrypt(self.api_key.replace('enc:', '').encode()).decode()
            decrypted_api_secret = old_cipher.decrypt(self.api_secret.replace('enc:', '').encode()).decode()
            decrypted_passphrase = old_cipher.decrypt(self.passphrase.replace('enc:', '').encode()).decode() if self.passphrase else ""
            self.api_key = 'enc:' + new_cipher.encrypt(decrypted_api_key.encode()).decode()
            self.api_secret = 'enc:' + new_cipher.encrypt(decrypted_api_secret.encode()).decode()
            self.passphrase = 'enc:' + new_cipher.encrypt(decrypted_passphrase.encode()).decode() if decrypted_passphrase else ""
            self.encryption_version = f'v{int(self.encryption_version.replace("v", "")) + 1}'
            self.save()
            logger.info(f"Ключ шифрования успешно ротирован для API-ключа ID {self.id} пользователя {self.user.username}")
        except Exception as e:
            logger.error(f"Ошибка ротации ключа шифрования для API-ключа ID {self.id}: {str(e)}")
            raise ValueError(f"Ошибка ротации ключа шифрования: {str(e)}")

    def __str__(self):
        return f"{self.exchange.capitalize()} Key for {self.user.username}"

class SignalType(models.TextChoices):
    PRICE = 'price', 'Цена'
    BASE_VOLUME = 'base_volume', 'Объём базовой'
    NOMINAL_VOLUME = 'nominal_volume', 'Объём номинальной'
    RSI = 'rsi', 'RSI'
    CCI = 'cci', 'CCI'
    MFI = 'mfi', 'MFI'
    MACD = 'macd', 'MACD'
    BOLLINGER_BANDS = 'bollinger_bands', 'Bollinger Bands'
    STOCHASTIC = 'stochastic', 'Stochastic'
    VOLUME_SPIKE = 'volume_spike', 'Volume Spike'
    MA_CROSSOVER = 'ma_crossover', 'Moving Average Crossover'
    PIVOT_POINTS = 'pivot_points', 'Pivot Points'
    ADX = 'adx', 'ADX'  # Добавлено
    ATR = 'atr', 'ATR'  # Добавлено
    ICHIMOKU = 'ichimoku', 'Ichimoku'  # Добавлено

class StrategyPreset(models.TextChoices):
    CONSERVATIVE = 'conservative', 'Консервативный'
    MODERATE = 'moderate', 'Умеренный'
    AGGRESSIVE = 'aggressive', 'Агрессивный'

def validate_additional_settings(value):
    if not isinstance(value, dict):
        logger.error("additional_settings не является словарем")
        raise ValidationError("additional_settings должен быть словарем.")
    if 'base_quantity' not in value:
        logger.error("Отсутствует base_quantity в additional_settings")
        raise ValidationError("Отсутствует обязательное поле 'base_quantity' в additional_settings.")
    if not isinstance(value['base_quantity'], (int, float)) or value['base_quantity'] <= 0:
        logger.error("base_quantity не является положительным числом")
        raise ValidationError("base_quantity должен быть положительным числом.")

def validate_signal_params(value):
    """Валидация параметров сигнала в зависимости от типа сигнала."""
    if not isinstance(value, dict):
        logger.error("signal_params не является словарем")
        raise ValidationError("signal_params должен быть словарем.")
    
    signal_type = value.get('type') if 'type' in value else None
    if not signal_type:
        return  # Если тип сигнала не указан (например, в одиночном сигнале), валидация не нужна

    required_params = {
        'rsi': ['period', 'threshold'],
        'cci': ['period', 'threshold'],
        'mfi': ['period', 'threshold'],
        'macd': ['fast_period', 'slow_period', 'signal_period', 'condition'],
        'bollinger_bands': ['period', 'dev'],
        'stochastic': ['k_period', 'd_period', 'threshold'],
        'volume_spike': ['lookback', 'threshold'],
        'ma_crossover': ['short_period', 'long_period'],
        'pivot_points': ['condition'],
        'adx': ['period', 'threshold'],
        'atr': ['period', 'threshold'],
        'ichimoku': ['tenkan_period', 'kijun_period', 'senkou_period', 'condition'],
        'price': ['target_price'],
    }

    if signal_type in required_params:
        for param in required_params[signal_type]:
            if param not in value:
                logger.error(f"Отсутствует обязательный параметр '{param}' для сигнала '{signal_type}'")
                raise ValidationError(f"Отсутствует обязательный параметр '{param}' для сигнала '{signal_type}'")

class Bot(models.Model):
    SPOT = 'spot'
    FUTURES = 'futures'
    MARGIN = 'margin'
    STRATEGY_CHOICES = [
        (SPOT, 'Spot Trading'),
        (FUTURES, 'Futures Trading'),
        (MARGIN, 'Margin Trading'),
    ]
    ALGORITHM_CHOICES = [
        ('long', 'Long'),
        ('short', 'Short'),
    ]
    TRADE_MODE_CHOICES = [
        ('order_grid', 'Order Grid'),
        ('martingale', 'Martingale'),
        ('custom', 'Custom'),
        ('dca', 'Dollar-Cost Averaging'),
        ('trailing_stop', 'Trailing Stop'),
        ('arbitrage', 'Arbitrage'),
    ]

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='bots',
        verbose_name="Пользователь"
    )
    name = models.CharField(
        max_length=255,
        blank=True,
        verbose_name="Название бота"
    )
    strategy = models.CharField(
        max_length=50,
        choices=STRATEGY_CHOICES,
        verbose_name="Стратегия"
    )
    algorithm = models.CharField(
        max_length=20,
        choices=ALGORITHM_CHOICES,
        default='long',
        verbose_name="Алгоритм"
    )
    api_key = models.ForeignKey(
        APIKey,
        on_delete=models.CASCADE,
        related_name='bots',
        verbose_name="Привязанный API-ключ"
    )
    trading_pair = models.CharField(
        max_length=50,
        verbose_name="Торговая пара",
        help_text="Например, BTC/USDT",
        blank=True,
        null=True
    )
    deposit = models.DecimalField(
        max_digits=15,
        decimal_places=2,
        verbose_name="Депозит",
        help_text="Сумма выделенного депозита для торговли",
        default=0.00
    )
    leverage = models.IntegerField(
        verbose_name="Кредитное плечо",
        help_text="Кредитное плечо, используемое ботом",
        default=1,
        validators=[MinValueValidator(1), MaxValueValidator(125)]
    )
    margin_type = models.CharField(
        max_length=20,
        choices=[('isolated', 'Isolated'), ('cross', 'Cross')],
        default='isolated',
        verbose_name="Тип маржи"
    )
    trade_mode = models.CharField(
        max_length=50,
        choices=TRADE_MODE_CHOICES,
        verbose_name="Режим торговли",
        help_text="Например, order_grid или martingale",
        default='order_grid'
    )
    additional_settings = models.JSONField(
        verbose_name="Дополнительные настройки",
        help_text="Дополнительные параметры бота",
        default=dict,
        validators=[validate_additional_settings]
    )
    status = models.CharField(
        max_length=20,
        choices=[('active', 'Active'), ('paused', 'Paused'), ('stopped', 'Stopped')],
        default='stopped',
        verbose_name="Статус"
    )
    is_running = models.BooleanField(
        default=False,
        verbose_name="Запущен ли бот"
    )
    test_mode = models.BooleanField(
        default=False,
        verbose_name="Тестовый режим",
        help_text="Запуск бота в режиме симуляции (без реальных сделок)"
    )
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="Дата создания")
    deals_completed = models.IntegerField(
        default=0,
        verbose_name="Завершенные сделки",
        help_text="Количество завершенных сделок ботом"
    )

    class Meta:
        unique_together = ('user', 'name')
        verbose_name = "Бот"
        verbose_name_plural = "Боты"
        indexes = [
            models.Index(fields=['user', 'status']),
            models.Index(fields=['trading_pair']),
        ]

    def generate_base_name(self):
        trading_pair_base = self.trading_pair.split('/')[0] if self.trading_pair else 'Unknown'
        return f"{self.api_key.exchange.capitalize()} {self.strategy.capitalize()} {trading_pair_base}"

    def get_unique_name(self, base_name):
        existing_names = set(Bot.objects.filter(user=self.user).values_list('name', flat=True))
        name = base_name
        counter = 2
        while name in existing_names:
            name = f"{base_name} ({counter})"
            counter += 1
        return name

    def save(self, *args, **kwargs):
        logger.info(f"Сохранение бота для пользователя {self.user}: name={self.name}, id={self.id}")
        if not self.pk and not self.name:
            base_name = self.generate_base_name()
            self.name = self.get_unique_name(base_name)
            logger.info(f"Генерация имени для нового бота: {self.name}")
        super().save(*args, **kwargs)
        logger.info(f"Бот сохранен: name={self.name}, id={self.id}")

    def __str__(self):
        return f"{self.name} ({self.strategy.capitalize()})"

class BotSettings(models.Model):
    bot = models.OneToOneField(
        Bot,
        on_delete=models.CASCADE,
        related_name='settings',
        verbose_name="Бот"
    )
    signal_type = models.CharField(
        max_length=20,
        choices=SignalType.choices,
        verbose_name="Тип сигнала",
        default=SignalType.PRICE
    )
    signal_params = models.JSONField(
        default=dict,
        verbose_name="Параметры сигнала",
        help_text="Например, {'period': 14, 'threshold': 30} для RSI",
        validators=[validate_signal_params]  # Добавлен валидатор
    )
    signal_interval = models.CharField(
        max_length=20,
        verbose_name="Интервал свечей для сигнала",
        help_text="Например, '1h' для часового интервала",
        default='1h',
        choices=[
            ('1m', '1 минута'),
            ('5m', '5 минут'),
            ('15m', '15 минут'),
            ('1h', '1 час'),
            ('4h', '4 часа'),
            ('1d', '1 день'),
        ]
    )
    task_interval = models.IntegerField(  # Новое поле
        verbose_name="Интервал выполнения задачи (минуты)",
        help_text="Как часто проверять сигнал (в минутах)",
        validators=[MinValueValidator(1)],
        default=60
    )
    take_profit = models.FloatField(
        verbose_name="Тейк-профит (%)",
        validators=[MinValueValidator(0.1), MaxValueValidator(100)],
        default=5.0
    )
    stop_loss = models.FloatField(
        verbose_name="Стоп-лосс (%)",
        validators=[MinValueValidator(0.1), MaxValueValidator(100)],
        default=5.0,
        null=True,
        blank=True
    )
    trailing_stop_percentage = models.FloatField(
        verbose_name="Процент Trailing Stop (%)",
        validators=[MinValueValidator(0.1), MaxValueValidator(100)],
        default=1.0,
        null=True,
        blank=True
    )
    dca_interval = models.IntegerField(
        verbose_name="Интервал DCA (минуты)",
        validators=[MinValueValidator(1)],
        default=60,
        null=True,
        blank=True
    )
    arbitrage_spread_threshold = models.FloatField(
        verbose_name="Порог спреда для арбитража (%)",
        validators=[MinValueValidator(0.1), MaxValueValidator(100)],
        default=0.5,
        null=True,
        blank=True,
        help_text="Минимальный спред между спотом и фьючерсами для арбитражной сделки"
    )
    pivot_points_period = models.CharField(
        max_length=20,
        verbose_name="Период для Pivot Points",
        help_text="Например, 'D' для дневного периода, 'W' для недельного",
        default='D',
        choices=[
            ('1h', '1 час'),
            ('4h', '4 часа'),
            ('D', 'День'),
            ('W', 'Неделя'),
            ('M', 'Месяц'),
        ]
    )
    ma_crossover_type = models.CharField(
        max_length=20,
        verbose_name="Тип скользящей средней",
        default='sma',
        choices=[
            ('sma', 'Простая (SMA)'),
            ('ema', 'Экспоненциальная (EMA)'),
        ]
    )
    grid_overlap = models.FloatField(
        verbose_name="Перекрытие сетки (%)",
        validators=[MinValueValidator(0), MaxValueValidator(100)],
        default=25.0
    )
    grid_orders = models.IntegerField(
        verbose_name="Количество ордеров в сетке",
        validators=[MinValueValidator(1)],
        default=10
    )
    martingale = models.FloatField(
        verbose_name="Коэффициент мартингейла (%)",
        validators=[MinValueValidator(0), MaxValueValidator(100)],
        default=5.0
    )
    grid_spacing = models.FloatField(
        verbose_name="Отступ между ордерами (%)",
        validators=[MinValueValidator(0.1), MaxValueValidator(100)],
        default=1.0
    )
    logarithmic_distribution = models.BooleanField(
        default=False,
        verbose_name="Логарифмическое распределение цен"
    )
    partial_grid = models.BooleanField(
        default=False,
        verbose_name="Частичное выставление сетки"
    )
    grid_follow = models.BooleanField(
        default=False,
        verbose_name="Подтяжка сетки"
    )
    stop_after_deals = models.BooleanField(
        default=False,
        verbose_name="Остановить бота после завершения сделок"
    )
    preset = models.CharField(
        max_length=20,
        choices=StrategyPreset.choices,
        null=True,
        blank=True,
        verbose_name="Предустановка"
    )
    combined_strategies = models.JSONField(
        verbose_name="Комбинация стратегий",
        default=list,
        blank=True,
        help_text="Список стратегий, например: ['order_grid', 'trailing_stop']"
    )
    combined_signals = models.JSONField(
        verbose_name="Комбинация сигналов",
        default=list,
        blank=True,
        help_text="Список сигналов, например: [{'type': 'rsi', 'threshold': 30}, {'type': 'macd', 'condition': 'crossover'}]",
        validators=[validate_signal_params]  # Добавлен валидатор
    )

    def apply_preset(self):
        if self.preset == StrategyPreset.CONSERVATIVE:
            self.grid_overlap = 40.0
            self.grid_orders = 20
            self.martingale = 5.0
            self.stop_loss = 5.0
            self.take_profit = 2.0
            self.trailing_stop_percentage = 1.0
            self.dca_interval = 120
            self.arbitrage_spread_threshold = 0.5
        elif self.preset == StrategyPreset.MODERATE:
            self.grid_overlap = 25.0
            self.grid_orders = 15
            self.martingale = 5.0
            self.stop_loss = 10.0
            self.take_profit = 5.0
            self.trailing_stop_percentage = 2.0
            self.dca_interval = 60
            self.arbitrage_spread_threshold = 0.5
        elif self.preset == StrategyPreset.AGGRESSIVE:
            self.grid_overlap = 15.0
            self.grid_orders = 10
            self.martingale = 5.0
            self.stop_loss = 20.0
            self.take_profit = 10.0
            self.trailing_stop_percentage = 5.0
            self.dca_interval = 30
            self.arbitrage_spread_threshold = 0.5
        self.save()
        logger.info(f"Применены настройки {self.preset} для бота {self.bot.id}")

    def __str__(self):
        return f"Settings for Bot {self.bot.name}"

class LogEntry(models.Model):
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='logs',
        verbose_name="Пользователь"
    )
    bot = models.ForeignKey(
        Bot,
        on_delete=models.CASCADE,
        related_name='logs',
        verbose_name="Бот"
    )
    action = models.CharField(max_length=255, verbose_name="Действие")
    details = models.TextField(verbose_name="Подробности")
    timestamp = models.DateTimeField(default=timezone.now, db_index=True, verbose_name="Время события")
    status = models.CharField(max_length=50, verbose_name="Статус")
    error_message = models.TextField(null=True, blank=True, verbose_name="Сообщение об ошибке")
    financial_result = models.JSONField(null=True, blank=True, verbose_name="Финансовые результаты")
    trade_id = models.CharField(max_length=100, null=True, blank=True, verbose_name="ID сделки")

    class Meta:
        indexes = [
            models.Index(fields=['user', 'timestamp']),
            models.Index(fields=['bot', 'timestamp']),
            models.Index(fields=['status']),
        ]
        verbose_name = "Лог"
        verbose_name_plural = "Логи"

    def __str__(self):
        return f"[{self.timestamp}] Bot {self.bot.name} (User {self.user.id}): {self.action} ({self.status})"

class BotPerformanceSummary(models.Model):
    bot = models.ForeignKey(
        Bot,
        on_delete=models.CASCADE,
        related_name='performance_summaries',
        verbose_name="Бот"
    )
    period_start = models.DateTimeField(verbose_name="Начало периода")
    period_end = models.DateTimeField(verbose_name="Конец периода")
    total_profit = models.DecimalField(max_digits=15, decimal_places=2, default=0.00, verbose_name="Общая прибыль")
    total_trades = models.IntegerField(default=0, verbose_name="Количество сделок")
    roi = models.FloatField(default=0.0, verbose_name="ROI (%)")

    class Meta:
        verbose_name = "Сводка по боту"
        verbose_name_plural = "Сводки по ботам"

    def __str__(self):
        return f"Summary for Bot {self.bot.name} ({self.period_start} - {self.period_end})"

class BotPosition(models.Model):
    bot = models.OneToOneField(
        Bot,
        on_delete=models.CASCADE,
        related_name='position',
        verbose_name="Бот"
    )
    position = models.FloatField(default=0, verbose_name="Текущая позиция")
    avg_price = models.FloatField(default=0, verbose_name="Средняя цена покупки")
    sell_order_id = models.CharField(max_length=100, null=True, blank=True, verbose_name="ID ордера на продажу")
    buy_orders = models.JSONField(default=list, verbose_name="Ордера на покупку")
    position_opened = models.BooleanField(default=False, verbose_name="Позиция открыта")
    highest_price = models.FloatField(null=True, blank=True, verbose_name="Максимальная цена")

    class Meta:
        verbose_name = "Позиция бота"
        verbose_name_plural = "Позиции ботов"

    def __str__(self):
        return f"Position for Bot {self.bot.name}"