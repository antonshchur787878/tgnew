# bots/serializers.py
from rest_framework import serializers
from .models import APIKey, Bot, BotSettings, SignalType, StrategyPreset
from .utils import ExchangeAPI
from django.db import IntegrityError
from django.core.cache import cache
from django.conf import settings  # Добавляем импорт для настроек
import logging

logger = logging.getLogger(__name__)

class APIKeySerializer(serializers.ModelSerializer):
    """
    Сериализатор для API-ключей.
    """
    api_key = serializers.CharField(write_only=True)
    api_secret = serializers.CharField(write_only=True)

    class Meta:
        model = APIKey
        fields = ['id', 'exchange', 'api_key', 'api_secret', 'added_at']
        extra_kwargs = {
            'api_key': {'write_only': True},
            'api_secret': {'write_only': True},
        }

    def validate(self, data):
        """
        Проверка уникальности и валидности данных API-ключа.
        """
        user = self.context['request'].user
        if APIKey.objects.filter(user=user, api_key=data['api_key']).exists():
            logger.warning(f"Попытка добавить уже существующий ключ для пользователя {user.username} (ID: {user.id})")
            raise serializers.ValidationError("Ключ с таким названием уже существует для текущего пользователя.")

        if not data.get('api_secret'):
            logger.error(f"API-секрет пустой при валидации для пользователя {user.username} (ID: {user.id})")
            raise serializers.ValidationError("API-секрет не может быть пустым.")

        try:
            ExchangeAPI.validate_api_key(
                exchange=data['exchange'],
                api_key=data['api_key'],
                api_secret=data['api_secret']
            )
            ExchangeAPI.check_api_key_permissions(
                exchange=data['exchange'],
                api_key=data['api_key'],
                api_secret=data['api_secret']
            )
            logger.info(f"API-ключ для {data['exchange']} успешно валидирован для пользователя {user.username} (ID: {user.id})")
        except Exception as e:
            logger.error(f"Ошибка проверки API-ключа для {data['exchange']} пользователя {user.username} (ID: {user.id}): {str(e)}")
            raise serializers.ValidationError(f"Ошибка проверки API-ключа: {str(e)}")

        return data

class BotSettingsSerializer(serializers.ModelSerializer):
    """
    Сериализатор для настроек бота.
    """
    class Meta:
        model = BotSettings
        fields = [
            'signal_type', 'signal_params', 'signal_interval', 'take_profit',
            'grid_overlap', 'grid_orders', 'martingale', 'grid_spacing',
            'logarithmic_distribution', 'partial_grid', 'grid_follow',
            'stop_after_deals', 'preset', 'stop_loss', 'combined_strategies',
            'combined_signals', 'trailing_stop_percentage', 'dca_interval'
        ]

    def validate_signal_type(self, value):
        """
        Проверяет, что тип сигнала соответствует допустимым значениям.
        """
        valid_signals = [choice[0] for choice in SignalType.choices]
        if value not in valid_signals:
            logger.error(f"Недопустимый тип сигнала: {value}. Допустимые: {valid_signals}")
            raise serializers.ValidationError(
                f"Недопустимый тип сигнала. Допустимые значения: {valid_signals}"
            )
        return value

    def validate_signal_params(self, value):
        """
        Проверяет параметры сигнала в зависимости от типа сигнала.
        """
        if not isinstance(value, dict):
            logger.error("signal_params не является словарем")
            raise serializers.ValidationError("signal_params должен быть словарем.")

        signal_type = self.initial_data.get('signal_type') or (self.instance.signal_type if self.instance else None)
        if signal_type in ['rsi', 'cci', 'mfi']:
            required_fields = ['period', 'threshold']
            for field in required_fields:
                if field not in value:
                    logger.error(f"Отсутствует поле '{field}' в signal_params для {signal_type}")
                    raise serializers.ValidationError(
                        f"Отсутствует обязательное поле '{field}' в signal_params для сигнала {signal_type}."
                    )
            if not isinstance(value['period'], int) or value['period'] <= 0:
                logger.error(f"period не является положительным целым числом для {signal_type}")
                raise serializers.ValidationError("period должен быть положительным целым числом.")
            if not isinstance(value['threshold'], (int, float)) or not 0 <= value['threshold'] <= 100:
                logger.error(f"threshold вне диапазона 0–100 для {signal_type}")
                raise serializers.ValidationError("threshold должен быть числом в диапазоне 0–100.")
        elif signal_type == 'price':
            if 'target_price' not in value:
                logger.error("Отсутствует target_price для сигнала price")
                raise serializers.ValidationError(
                    "Отсутствует обязательное поле 'target_price' в signal_params для сигнала price."
                )
            if not isinstance(value['target_price'], (int, float)) or value['target_price'] <= 0:
                logger.error("target_price не является положительным числом для price")
                raise serializers.ValidationError("target_price должен быть положительным числом.")
        elif signal_type in ['base_volume', 'nominal_volume', 'volume_spike']:
            if 'threshold' not in value:
                logger.error(f"Отсутствует threshold для сигнала {signal_type}")
                raise serializers.ValidationError(
                    f"Отсутствует обязательное поле 'threshold' в signal_params для сигнала {signal_type}."
                )
            if not isinstance(value['threshold'], (int, float)) or value['threshold'] <= 0:
                logger.error(f"threshold не является положительным числом для {signal_type}")
                raise serializers.ValidationError("threshold должен быть положительным числом.")
            if signal_type == 'volume_spike' and 'lookback' not in value:
                logger.error("Отсутствует lookback для volume_spike")
                raise serializers.ValidationError(
                    "Отсутствует обязательное поле 'lookback' в signal_params для volume_spike."
                )
            if signal_type == 'volume_spike' and (not isinstance(value['lookback'], int) or value['lookback'] <= 0):
                logger.error("lookback не является положительным целым числом для volume_spike")
                raise serializers.ValidationError("lookback должен быть положительным целым числом.")
        elif signal_type == 'macd':
            required_fields = ['fast_period', 'slow_period', 'signal_period']
            for field in required_fields:
                if field not in value:
                    logger.error(f"Отсутствует поле '{field}' в signal_params для {signal_type}")
                    raise serializers.ValidationError(
                        f"Отсутствует обязательное поле '{field}' в signal_params для сигнала {signal_type}."
                    )
            for field in required_fields:
                if not isinstance(value[field], int) or value[field] <= 0:
                    logger.error(f"{field} не является положительным целым числом для {signal_type}")
                    raise serializers.ValidationError(f"{field} должен быть положительным целым числом.")
        elif signal_type == 'bollinger_bands':
            required_fields = ['period', 'dev']
            for field in required_fields:
                if field not in value:
                    logger.error(f"Отсутствует поле '{field}' в signal_params для {signal_type}")
                    raise serializers.ValidationError(
                        f"Отсутствует обязательное поле '{field}' в signal_params для сигнала {signal_type}."
                    )
            if not isinstance(value['period'], int) or value['period'] <= 0:
                logger.error("period не является положительным целым числом для bollinger_bands")
                raise serializers.ValidationError("period должен быть положительным целым числом.")
            if not isinstance(value['dev'], (int, float)) or value['dev'] <= 0:
                logger.error("dev не является положительным числом для bollinger_bands")
                raise serializers.ValidationError("dev должен быть положительным числом.")
        elif signal_type == 'stochastic':
            required_fields = ['k_period', 'd_period', 'threshold']
            for field in required_fields:
                if field not in value:
                    logger.error(f"Отсутствует поле '{field}' в signal_params для {signal_type}")
                    raise serializers.ValidationError(
                        f"Отсутствует обязательное поле '{field}' в signal_params для сигнала {signal_type}."
                    )
            for field in ['k_period', 'd_period']:
                if not isinstance(value[field], int) or value[field] <= 0:
                    logger.error(f"{field} не является положительным целым числом для {signal_type}")
                    raise serializers.ValidationError(f"{field} должен быть положительным целым числом.")
            if not isinstance(value['threshold'], (int, float)) or not 0 <= value['threshold'] <= 100:
                logger.error("threshold вне диапазона 0–100 для stochastic")
                raise serializers.ValidationError("threshold должен быть числом в диапазоне 0–100.")
        elif signal_type == 'ma_crossover':
            required_fields = ['short_period', 'long_period']
            for field in required_fields:
                if field not in value:
                    logger.error(f"Отсутствует поле '{field}' в signal_params для {signal_type}")
                    raise serializers.ValidationError(
                        f"Отсутствует обязательное поле '{field}' в signal_params для сигнала {signal_type}."
                    )
            for field in required_fields:
                if not isinstance(value[field], int) or value[field] <= 0:
                    logger.error(f"{field} не является положительным целым числом для {signal_type}")
                    raise serializers.ValidationError(f"{field} должен быть положительным целым числом.")
        elif signal_type == 'pivot_points':
            if 'condition' not in value:
                logger.error("Отсутствует condition для сигнала pivot_points")
                raise serializers.ValidationError(
                    "Отсутствует обязательное поле 'condition' в signal_params для сигнала pivot_points."
                )
            if value['condition'] not in ['above_resistance', 'below_support']:
                logger.error(f"Недопустимое значение condition: {value['condition']} для pivot_points")
                raise serializers.ValidationError(
                    "condition должен быть 'above_resistance' или 'below_support'."
                )
        elif signal_type == 'ichimoku':  # Добавляем валидацию для Ichimoku
            required_fields = ['tenkan_period', 'kijun_period', 'senkou_period']
            for field in required_fields:
                if field not in value:
                    logger.error(f"Отсутствует поле '{field}' в signal_params для {signal_type}")
                    raise serializers.ValidationError(
                        f"Отсутствует обязательное поле '{field}' в signal_params для сигнала {signal_type}."
                    )
            for field in required_fields:
                if not isinstance(value[field], int) or value[field] <= 0:
                    logger.error(f"{field} не является положительным целым числом для {signal_type}")
                    raise serializers.ValidationError(f"{field} должен быть положительным целым числом.")
        logger.debug(f"signal_params успешно валидирован: {value}")
        return value

    def validate_signal_interval(self, value):
        """
        Проверяет, что интервал сигнала соответствует допустимым значениям (например, для Bybit).
        """
        valid_intervals = ['1', '3', '5', '15', '30', '60', '120', '240', '360', '720', 'D', 'W', 'M']
        interval_mapping = {
            '1m': '1', '3m': '3', '5m': '5', '15m': '15', '30m': '30',
            '1h': '60', '2h': '120', '4h': '240', '6h': '360', '12h': '720',
            '1d': 'D', '1w': 'W', '1M': 'M',
            '1 минута': '1', '3 минуты': '3', '5 минут': '5', '15 минут': '15', '30 минут': '30',
            '1 час': '60', '2 часа': '120', '4 часа': '240', '6 часов': '360', '12 часов': '720',
            '1 день': 'D', '1 неделя': 'W', '1 месяц': 'M'
        }
        value = str(value).lower()
        if value in interval_mapping:
            value = interval_mapping[value]
        if value not in valid_intervals:
            logger.error(f"Недопустимый интервал сигнала: {value}. Допустимые: {valid_intervals}")
            raise serializers.ValidationError(
                f"Интервал сигнала должен быть одним из следующих: {', '.join(valid_intervals)}."
            )
        return value

    def validate_take_profit(self, value):
        """
        Проверяет, что тейк-профит — положительное число.
        """
        if not isinstance(value, (int, float)) or value <= 0:
            logger.error("take_profit не является положительным числом")
            raise serializers.ValidationError("take_profit должен быть положительным числом.")
        return value

    def validate_grid_overlap(self, value):
        """
        Проверяет, что перекрытие сетки — положительное число.
        """
        if not isinstance(value, (int, float)) or value < 0:
            logger.error("grid_overlap отрицательное")
            raise serializers.ValidationError("grid_overlap должен быть неотрицательным числом.")
        return value

    def validate_grid_orders(self, value):
        """
        Проверяет, что количество ордеров в сетке — положительное целое число.
        """
        if not isinstance(value, int) or value <= 0:
            logger.error("grid_orders не является положительным целым числом")
            raise serializers.ValidationError("grid_orders должен быть положительным целым числом.")
        return value

    def validate_martingale(self, value):
        """
        Проверяет, что коэффициент мартингейла — положительное число.
        """
        if not isinstance(value, (int, float)) or value < 0:
            logger.error("martingale отрицательное")
            raise serializers.ValidationError("martingale должен быть неотрицательным числом.")
        return value

    def validate_grid_spacing(self, value):
        """
        Проверяет, что отступ между ордерами — положительное число.
        """
        if not isinstance(value, (int, float)) or value <= 0:
            logger.error("grid_spacing не является положительным числом")
            raise serializers.ValidationError("grid_spacing должен быть положительным числом.")
        return value

    def validate_logarithmic_distribution(self, value):
        """
        Проверяет, что logarithmic_distribution — булево значение.
        """
        if not isinstance(value, bool):
            logger.error("logarithmic_distribution не является булевым значением")
            raise serializers.ValidationError("logarithmic_distribution должен быть булевым значением (True или False).")
        return value

    def validate_partial_grid(self, value):
        """
        Проверяет, что partial_grid — булево значение.
        """
        if not isinstance(value, bool):
            logger.error("partial_grid не является булевым значением")
            raise serializers.ValidationError("partial_grid должен быть булевым значением (True или False).")
        return value

    def validate_grid_follow(self, value):
        """
        Проверяет, что grid_follow — булево значение.
        """
        if not isinstance(value, bool):
            logger.error("grid_follow не является булевым значением")
            raise serializers.ValidationError("grid_follow должен быть булевым значением (True или False).")
        return value

    def validate_stop_after_deals(self, value):
        """
        Проверяет, что stop_after_deals является булевым значением.
        """
        if not isinstance(value, bool):
            logger.error("stop_after_deals не является булевым значением")
            raise serializers.ValidationError("stop_after_deals должен быть булевым значением (True или False).")
        return value

    def validate_preset(self, value):
        """
        Проверяет, что предустановка соответствует допустимым значениям.
        """
        if value is None:
            return value
        valid_presets = [choice[0] for choice in StrategyPreset.choices]
        if value not in valid_presets:
            logger.error(f"Недопустимая предустановка: {value}. Допустимые: {valid_presets}")
            raise serializers.ValidationError(
                f"Недопустимая предустановка. Допустимые значения: {valid_presets}"
            )
        return value

    def validate_stop_loss(self, value):
        """
        Проверяет, что стоп-лосс — положительное число, если указано.
        """
        if value is None:
            return value
        if not isinstance(value, (int, float)) or value <= 0:
            logger.error("stop_loss не является положительным числом")
            raise serializers.ValidationError("stop_loss должен быть положительным числом.")
        return value

    def validate_combined_strategies(self, value):
        """
        Проверяет, что комбинация стратегий — список допустимых значений.
        """
        valid_strategies = [choice[0] for choice in Bot.TRADE_MODE_CHOICES]
        if not isinstance(value, list):
            logger.error("combined_strategies не является списком")
            raise serializers.ValidationError("combined_strategies должен быть списком.")
        for strategy in value:
            if strategy not in valid_strategies:
                logger.error(f"Недопустимая стратегия в комбинации: {strategy}. Допустимые: {valid_strategies}")
                raise serializers.ValidationError(f"Недопустимая стратегия в комбинации: {strategy}")
        return value

    def validate_combined_signals(self, value):
        """
        Проверяет, что комбинация сигналов — список словарей с корректными параметрами.
        """
        valid_signal_types = [choice[0] for choice in SignalType.choices]
        if not isinstance(value, list):
            logger.error("combined_signals не является списком")
            raise serializers.ValidationError("combined_signals должен быть списком словарей.")
        for signal in value:
            if not isinstance(signal, dict) or 'type' not in signal:
                logger.error("Каждый сигнал в combined_signals должен быть словарем с полем 'type'")
                raise serializers.ValidationError("Каждый сигнал должен быть словарем с полем 'type'.")
            if signal['type'] not in valid_signal_types:
                logger.error(f"Недопустимый тип сигнала в комбинации: {signal['type']}. Допустимые: {valid_signal_types}")
                raise serializers.ValidationError(f"Недопустимый тип сигнала в комбинации: {signal['type']}")
            if signal['type'] == 'rsi':
                if 'threshold' not in signal or not isinstance(signal['threshold'], (int, float)):
                    logger.error("RSI сигнал требует валидный threshold")
                    raise serializers.ValidationError("RSI сигнал требует валидный threshold.")
            elif signal['type'] == 'macd':
                if 'condition' not in signal or signal['condition'] not in ['crossover', 'crossunder']:
                    logger.error("MACD сигнал требует валидное условие (crossover или crossunder)")
                    raise serializers.ValidationError("MACD сигнал требует валидное условие (crossover или crossunder).")
            elif signal['type'] == 'volume_spike':
                if 'threshold' not in signal or 'lookback' not in signal:
                    logger.error("Volume Spike сигнал требует threshold и lookback")
                    raise serializers.ValidationError("Volume Spike сигнал требует threshold и lookback.")
            elif signal['type'] == 'ma_crossover':
                if 'short_period' not in signal or 'long_period' not in signal:
                    logger.error("MA Crossover сигнал требует short_period и long_period")
                    raise serializers.ValidationError("MA Crossover сигнал требует short_period и long_period.")
            elif signal['type'] == 'pivot_points':
                if 'condition' not in signal or signal['condition'] not in ['above_resistance', 'below_support']:
                    logger.error("Pivot Points сигнал требует валидное condition")
                    raise serializers.ValidationError("Pivot Points сигнал требует валидное condition (above_resistance или below_support).")
            elif signal['type'] == 'ichimoku':  # Добавляем валидацию для Ichimoku
                if 'condition' not in signal or signal['condition'] not in ['above_cloud', 'below_cloud']:
                    logger.error("Ichimoku сигнал требует валидное condition (above_cloud или below_cloud)")
                    raise serializers.ValidationError("Ichimoku сигнал требует валидное condition (above_cloud или below_cloud).")
        return value

    def validate_trailing_stop_percentage(self, value):
        """
        Проверяет, что процент Trailing Stop — положительное число, если указано.
        """
        if value is None:
            return value
        if not isinstance(value, (int, float)) or value <= 0:
            logger.error("trailing_stop_percentage не является положительным числом")
            raise serializers.ValidationError("trailing_stop_percentage должен быть положительным числом.")
        return value

    def validate_dca_interval(self, value):
        """
        Проверяет, что интервал DCA — положительное целое число, если указано.
        """
        if value is None:
            return value
        if not isinstance(value, int) or value <= 0:
            logger.error("dca_interval не является положительным целым числом")
            raise serializers.ValidationError("dca_interval должен быть положительным целым числом.")
        return value

class BotSerializer(serializers.ModelSerializer):
    """
    Сериализатор для создания и управления ботами.
    """
    api_key_id = serializers.IntegerField(
        required=True,
        write_only=True,
        label="Существующий API-ключ"
    )
    settings = BotSettingsSerializer(required=True)

    class Meta:
        model = Bot
        fields = [
            'id', 'name', 'strategy', 'algorithm', 'api_key_id',
            'trading_pair', 'deposit', 'leverage', 'margin_type', 'trade_mode',
            'additional_settings', 'status', 'is_running', 'test_mode', 'created_at', 'settings'
        ]
        read_only_fields = ['id', 'created_at', 'is_running']
        extra_kwargs = {'name': {'required': False}}

    def validate_name(self, value):
        """
        Автоматически генерирует имя бота, если оно не указано.
        """
        if not value and self.initial_data.get('trading_pair'):
            trading_pair_base = self.initial_data['trading_pair'].split('/')[0]
            api_key_id = self.initial_data['api_key_id']
            try:
                api_key = APIKey.objects.get(id=api_key_id)
                exchange = api_key.exchange
            except APIKey.DoesNotExist:
                logger.error("API-ключ с ID {api_key_id} не найден")
                raise serializers.ValidationError("API-ключ с указанным ID не найден.")
            strategy = self.initial_data.get('strategy', 'spot')
            generated_name = f"{exchange.capitalize()} {strategy.capitalize()} {trading_pair_base}"
            logger.info(f"Сгенерировано имя бота: {generated_name}")
            return generated_name
        return value

    def validate_trading_pair(self, value):
        """
        Проверяет, доступна ли указанная торговая пара на выбранной бирже.
        Преобразует 'BTC/USDT' в 'BTCUSDT' перед валидацией.
        """
        if not value:
            logger.error("Торговая пара не указана")
            raise serializers.ValidationError("Торговая пара не может быть пустой.")

        original_value = value
        value = value.replace('/', '') if '/' in value else value
        logger.info(f"Преобразование торговой пары: '{original_value}' -> '{value}'")

        api_key_id = self.initial_data.get('api_key_id')
        try:
            api_key = APIKey.objects.get(id=api_key_id)
            exchange = api_key.exchange
        except APIKey.DoesNotExist:
            logger.error(f"API-ключ с ID {api_key_id} не найден")
            raise serializers.ValidationError("API-ключ с указанным ID не найден.")

        cache_key = f"trading_pairs_{exchange}"
        trading_pairs = cache.get(cache_key)
        if trading_pairs is not None:
            logger.debug(f"Торговые пары для {exchange} извлечены из кэша")
        else:
            try:
                trading_pairs = ExchangeAPI.get_trading_pairs(exchange)
                cache.set(cache_key, trading_pairs, timeout=settings.TRADING_PAIRS_CACHE_TIMEOUT)
                logger.info(f"Торговые пары для {exchange} закэшированы на {settings.TRADING_PAIRS_CACHE_TIMEOUT} секунд")
            except Exception as e:
                logger.error(f"Ошибка при запросе торговых пар для {exchange}: {str(e)}")
                raise serializers.ValidationError(f"Ошибка при запросе торговых пар: {str(e)}")

        if value not in trading_pairs:
            logger.warning(f"Торговая пара {value} недоступна на бирже {exchange}")
            raise serializers.ValidationError(f"Торговая пара {value} недоступна на бирже {exchange}.")
        return original_value

    def validate_api_key_id(self, value):
        """
        Проверяет валидность API-ключа и его принадлежность пользователю.
        """
        try:
            api_key_obj = APIKey.objects.get(id=value)
            user = self.context['request'].user
            if api_key_obj.user != user:
                logger.error(f"API-ключ ID {value} не принадлежит пользователю {user.username} (ID: {user.id})")
                raise serializers.ValidationError("Этот API-ключ не принадлежит вам.")
            decrypted_keys = api_key_obj.get_decrypted_keys()
            ExchangeAPI.validate_api_key(
                exchange=api_key_obj.exchange,
                api_key=decrypted_keys["api_key"],
                api_secret=decrypted_keys["api_secret"]
            )
            return value
        except APIKey.DoesNotExist:
            logger.error(f"API-ключ с ID {value} не найден")
            raise serializers.ValidationError("API-ключ с указанным ID не найден.")
        except Exception as e:
            logger.error(f"Ошибка проверки API-ключа с ID {value}: {str(e)}")
            raise serializers.ValidationError(f"Ошибка проверки API-ключа: {str(e)}")

    def validate_deposit(self, value):
        """
        Проверяет, достаточно ли средств на балансе пользователя для депозита.
        """
        api_key_id = self.initial_data.get('api_key_id')
        try:
            api_key_obj = APIKey.objects.get(id=api_key_id)
            decrypted_keys = api_key_obj.get_decrypted_keys()
            balance_data = ExchangeAPI.get_balance(
                api_key_obj.exchange,
                decrypted_keys["api_key"],
                decrypted_keys["api_secret"],
                category='futures' if self.initial_data.get('strategy') == 'futures' else 'spot'
            )
            total_available_balance = float(balance_data['total_available_balance'])
            if total_available_balance < float(value):
                logger.warning(f"Недостаточно средств для депозита: баланс={total_available_balance}, требуется={value}")
                raise serializers.ValidationError(
                    f"Недостаточно средств. Баланс: {total_available_balance}, требуется: {value}."
                )
        except Exception as e:
            logger.error(f"Ошибка при проверке баланса для API-ключа ID {api_key_id}: {str(e)}")
            raise serializers.ValidationError(f"Ошибка при проверке баланса: {str(e)}")
        return value

    def validate_leverage(self, value):
        """
        Проверяет, что leverage — положительное число.
        """
        if not isinstance(value, (int, float)) or value <= 0:
            logger.error(f"leverage не является положительным числом: {value}")
            raise serializers.ValidationError("leverage должен быть положительным числом.")
        return value

    def validate_margin_type(self, value):
        """
        Проверяет, что margin_type соответствует допустимым значениям.
        """
        valid_margin_types = ['isolated', 'cross']
        if value not in valid_margin_types:
            logger.error(f"Недопустимый тип маржи: {value}. Допустимые: {valid_margin_types}")
            raise serializers.ValidationError(f"Недопустимый тип маржи. Допустимые значения: {valid_margin_types}")
        return value

    def validate_trade_mode(self, value):
        """
        Проверяет, что trade_mode соответствует допустимым значениям.
        """
        valid_modes = [choice[0] for choice in Bot.TRADE_MODE_CHOICES]
        if value not in valid_modes:
            logger.error(f"Недопустимый режим торговли: {value}. Допустимые: {valid_modes}")
            raise serializers.ValidationError(f"Недопустимый режим торговли. Допустимые значения: {valid_modes}")
        return value

    def validate_additional_settings(self, value):
        """
        Проверяет параметры additional_settings.
        """
        if not isinstance(value, dict):
            logger.error("additional_settings не является словарем")
            raise serializers.ValidationError("additional_settings должен быть словарем.")
        if 'base_quantity' not in value:
            logger.error("Отсутствует base_quantity в additional_settings")
            raise serializers.ValidationError(
                "Отсутствует обязательное поле 'base_quantity' в additional_settings."
            )
        if not isinstance(value['base_quantity'], (int, float)) or value['base_quantity'] <= 0:
            logger.error("base_quantity не является положительным числом")
            raise serializers.ValidationError("base_quantity должен быть положительным числом.")
        return value

    def validate(self, data):
        """
        Дополнительная валидация данных перед созданием бота.
        """
        request = self.context.get('request')
        if request and request.user:
            api_key_id = data.get('api_key_id')
            try:
                api_key = APIKey.objects.get(id=api_key_id)
                if api_key.user != request.user:
                    logger.error(f"API-ключ ID {api_key_id} не принадлежит пользователю {request.user.username} (ID: {request.user.id})")
                    raise serializers.ValidationError(
                        "API-ключ не принадлежит текущему пользователю."
                    )
            except APIKey.DoesNotExist:
                logger.error(f"API-ключ с ID {api_key_id} не существует")
                raise serializers.ValidationError("API-ключ с указанным ID не существует.")
        return data

    def create(self, validated_data):
        """
        Создаёт нового бота с привязанным API-ключом и настройками.
        """
        try:
            api_key_id = validated_data.pop('api_key_id')
            settings_data = validated_data.pop('settings')
            api_key = APIKey.objects.get(pk=api_key_id)
            bot = Bot.objects.create(api_key=api_key, **validated_data)
            BotSettings.objects.create(bot=bot, **settings_data)
            logger.info(f"Бот с ID {bot.id} успешно создан для пользователя {bot.api_key.user.username} (ID: {bot.api_key.user.id})")
            return bot
        except IntegrityError:
            logger.error(f"Бот с именем {validated_data.get('name')} уже существует")
            raise serializers.ValidationError("Бот с таким именем уже существует для этого пользователя.")
        except Exception as e:
            logger.error(f"Ошибка при создании бота с данными {validated_data}: {str(e)}")
            raise serializers.ValidationError(f"Ошибка при создании бота: {str(e)}")

    def update(self, instance, validated_data):
        """
        Обновляет бота и его настройки.
        """
        try:
            api_key_id = validated_data.pop('api_key_id', None)
            settings_data = validated_data.pop('settings', None)
            if api_key_id:
                api_key = APIKey.objects.get(id=api_key_id)
                instance.api_key = api_key
            instance = super().update(instance, validated_data)
            if settings_data:
                settings_serializer = BotSettingsSerializer(instance.settings, data=settings_data)
                settings_serializer.is_valid(raise_exception=True)
                settings_serializer.save()
            logger.info(f"Бот с ID {instance.id} успешно обновлён для пользователя {instance.api_key.user.username} (ID: {instance.api_key.user.id})")
            return instance
        except Exception as e:
            logger.error(f"Ошибка при обновлении бота с ID {instance.id}: {str(e)}")
            raise serializers.ValidationError(f"Ошибка при обновлении бота: {str(e)}")