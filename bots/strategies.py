# bots/strategies.py
import requests
import hmac
import hashlib
import logging
import json
import time
from django.conf import settings
from django.core.cache import cache
from .indicators import (
    calculate_rsi, calculate_cci, calculate_mfi,
    calculate_macd, calculate_bollinger_bands, calculate_stochastic,
    calculate_adx, calculate_atr, calculate_ichimoku,
    calculate_volume_spike, calculate_ma_crossover, calculate_pivot_points
)
from .models import Bot, BotSettings, BotPosition
from .utils import get_bybit_server_time, ExchangeAPI, safe_float
from celery import shared_task
from urllib.parse import urlencode
import math
import statistics
import numpy as np

logger = logging.getLogger(__name__)

class TradingStrategy:
    def __init__(self, bot):
        """
        Инициализирует стратегию торговли с использованием данных бота.

        Args:
            bot (Bot): Экземпляр модели Bot.
        """
        self.bot = bot
        self.exchange = bot.api_key.exchange.lower()
        decrypted_keys = bot.api_key.get_decrypted_keys()
        self.api_key = decrypted_keys['api_key']
        self.api_secret = decrypted_keys['api_secret']
        self.settings = bot.settings
        self.take_profit = self.settings.take_profit / 100
        self.stop_loss = (self.settings.stop_loss / 100) if self.settings.stop_loss else None
        self.trailing_stop_percentage = (self.settings.trailing_stop_percentage / 100) if self.settings.trailing_stop_percentage else None
        self.dca_interval = self.settings.dca_interval or 60
        self.grid_overlap = self.settings.grid_overlap / 100
        self.grid_orders = self.settings.grid_orders
        self.martingale = self.settings.martingale / 100
        self.grid_spacing = self.settings.grid_spacing / 100
        self.logarithmic = self.settings.logarithmic_distribution
        self.partial_grid = self.settings.partial_grid
        self.grid_follow = self.settings.grid_follow
        self.stop_after_deals = self.settings.stop_after_deals
        self.signal_type = self.settings.signal_type
        self.signal_params = self.settings.signal_params or {}
        self.signal_interval = self.settings.signal_interval
        self.combined_signals = self.settings.combined_signals or []
        self.combined_strategies = self.settings.combined_strategies or []
        self.position_obj, _ = BotPosition.objects.get_or_create(bot=bot)
        self.position = self.position_obj.position
        self.avg_price = self.position_obj.avg_price
        self.sell_order_id = self.position_obj.sell_order_id
        self.buy_orders = self.position_obj.buy_orders if self.position_obj.buy_orders else []
        self.position_opened = self.position_obj.position_opened
        self.highest_price = self.position_obj.highest_price or self.avg_price
        self.recv_window = getattr(settings, 'API_RECV_WINDOW', 10000)
        self.category = 'linear' if self.bot.strategy == 'futures' else 'spot'
        logger.debug("Инициализирована стратегия для бота %s (пользователь %s): exchange=%s, trading_pair=%s, category=%s",
                     bot.id, bot.api_key.user.username, self.exchange, bot.trading_pair, self.category)

    def execute(self):
        """
        Выполняет одну итерацию стратегии торговли.
        """
        logger.info("Выполнение стратегии для бота %s, trade_mode=%s, combined_strategies=%s",
                    self.bot.id, self.bot.trade_mode, self.combined_strategies)
        try:
            # Проверяем баланс и маржу для фьючерсов
            balance_data = ExchangeAPI.get_balance(
                self.exchange, self.api_key, self.api_secret, category=self.category
            )
            if self.category == 'futures':
                margin_ratio = balance_data.get('margin_data', {}).get('margin_ratio', 0)
                available_balance = balance_data.get('available_balance', 0)
                if margin_ratio > 0.9:
                    logger.warning(f"Высокий коэффициент маржи: {margin_ratio}. Остановка бота {self.bot.id}")
                    self.stop_bot()
                    return
                if available_balance <= 0:
                    logger.warning(f"Недостаточно средств для торговли фьючерсами: {available_balance}. Остановка бота {self.bot.id}")
                    self.stop_bot()
                    return

            # Выполняем комбинированные стратегии, если указаны
            if self.combined_strategies:
                for strategy in self.combined_strategies:
                    self.bot.trade_mode = strategy
                    self.run_strategy()
            else:
                self.run_strategy()

            # Проверяем стоп-лосс
            if self.stop_loss and self.position > 0:
                self.check_stop_loss()

            # Проверяем, нужно ли остановить бота после сделок
            if self.stop_after_deals and self.bot.deals_completed >= self.stop_after_deals:
                logger.info(f"Бот {self.bot.id} остановлен после завершения {self.bot.deals_completed} сделок")
                self.stop_bot()

        except Exception as e:
            logger.error("Ошибка при выполнении стратегии для бота %s: %s", self.bot.id, str(e), exc_info=True)
            raise

    def run_strategy(self):
        """
        Выполняет выбранную стратегию торговли.
        """
        strategy_map = {
            'order_grid': self.run_advanced_grid,
            'martingale': self.run_martingale,
            'dca': self.run_dca,
            'trailing_stop': self.run_trailing_stop,
            'arbitrage': self.run_arbitrage,
            'custom': self.run_custom
        }
        strategy_func = strategy_map.get(self.bot.trade_mode)
        if strategy_func:
            strategy_func()
        else:
            logger.error(f"Неизвестный режим торговли для бота {self.bot.id}: {self.bot.trade_mode}")
            raise ValueError(f"Unknown trade_mode: {self.bot.trade_mode}")

    def check_signal(self):
        """
        Проверяет, сработал ли сигнал или комбинация сигналов для запуска стратегии.

        Returns:
            bool: True, если сигнал(ы) сработал(и), иначе False.
        """
        if self.combined_signals:
            return self.check_combined_signal()
        else:
            return self.check_single_signal(self.signal_type, self.signal_params)

    def check_combined_signal(self):
        """
        Проверяет комбинацию сигналов.

        Returns:
            bool: True, если все сигналы сработали, иначе False.
        """
        signals = self.settings.combined_signals  # Например, [{'type': 'rsi', 'threshold': 30}, {'type': 'macd', 'condition': 'crossover'}]
        results = []
        klines = self.get_klines(self.signal_interval, limit=100)
        if not klines:
            logger.warning(f"Не удалось получить свечи для проверки комбинированных сигналов для бота {self.bot.id}")
            return False

        closes = [safe_float(kline[4]) for kline in klines]
        highs = [safe_float(kline[2]) for kline in klines]
        lows = [safe_float(kline[3]) for kline in klines]
        volumes = [safe_float(kline[5]) for kline in klines]

        for signal in signals:
            signal_type = signal.get('type')
            if signal_type == 'rsi':
                period = signal.get('period', 14)
                threshold = signal.get('threshold', 30)
                rsi = calculate_rsi(tuple(closes), period)
                if rsi is None:
                    logger.warning(f"RSI не рассчитан для бота {self.bot.id}, недостаточно данных")
                    results.append(False)
                    continue
                results.append(rsi < threshold)
                logger.debug(f"Комбинированный сигнал RSI для бота {self.bot.id}: RSI={rsi}, threshold={threshold}, result={rsi < threshold}")
            elif signal_type == 'macd':
                fast_period = signal.get('fast_period', 12)
                slow_period = signal.get('slow_period', 26)
                signal_period = signal.get('signal_period', 9)
                condition = signal.get('condition', 'crossover')
                macd, signal_line, _ = calculate_macd(tuple(closes), fast_period, slow_period, signal_period)
                if macd is None or signal_line is None:
                    logger.warning(f"MACD не рассчитан для бота {self.bot.id}, недостаточно данных")
                    results.append(False)
                    continue
                if len(closes) < 2:
                    logger.warning(f"Недостаточно данных для проверки MACD crossover для бота {self.bot.id}")
                    results.append(False)
                    continue
                prev_macd, prev_signal_line, _ = calculate_macd(tuple(closes[:-1]), fast_period, slow_period, signal_period)
                crossover = macd > signal_line and prev_macd <= prev_signal_line
                result = crossover if condition == 'crossover' else False
                results.append(result)
                logger.debug(f"Комбинированный сигнал MACD для бота {self.bot.id}: MACD={macd}, Signal={signal_line}, crossover={crossover}")
            elif signal_type == 'ma_crossover':
                short_period = signal.get('short_period', 10)
                long_period = signal.get('long_period', 20)
                short_ma, long_ma, prev_short_ma, prev_long_ma = calculate_ma_crossover(
                    tuple(closes), short_period, long_period, ma_type=self.settings.ma_crossover_type
                )
                if any(v is None for v in [short_ma, long_ma, prev_short_ma, prev_long_ma]):
                    logger.warning(f"MA Crossover не рассчитан для бота {self.bot.id}, недостаточно данных")
                    results.append(False)
                    continue
                logger.debug(f"Комбинированный сигнал MA Crossover для бота {self.bot.id}: Short={short_ma}, Long={long_ma}, Prev Short={prev_short_ma}, Prev Long={prev_long_ma}")
                results.append(short_ma > long_ma and prev_short_ma <= prev_long_ma)
            elif signal_type == 'pivot_points':
                pivot, r1, s1 = calculate_pivot_points(
                    tuple(highs), tuple(lows), tuple(closes), period=self.settings.pivot_points_period
                )
                if any(v is None for v in [pivot, r1, s1]):
                    logger.warning(f"Pivot Points не рассчитаны для бота {self.bot.id}, недостаточно данных")
                    results.append(False)
                    continue
                current_price = closes[-1]
                condition = signal.get('condition', 'above_resistance')
                logger.debug(f"Комбинированный сигнал Pivot Points для бота {self.bot.id}: Pivot={pivot}, R1={r1}, S1={s1}, Price={current_price}, Condition={condition}")
                results.append(current_price > r1 if condition == 'above_resistance' else current_price < s1)
            else:
                # Для других типов сигналов используем check_single_signal
                result = self.check_single_signal(signal_type, signal)
                results.append(result)
                logger.debug(f"Комбинированный сигнал {signal_type} для бота {self.bot.id}: result={result}")

        return all(results)

    def check_single_signal(self, signal_type, signal_params):
        """
        Проверяет отдельный сигнал.

        Args:
            signal_type (str): Тип сигнала.
            signal_params (dict): Параметры сигнала.

        Returns:
            bool: True, если сигнал сработал, иначе False.
        """
        klines = self.get_klines(self.signal_interval, limit=100)
        if not klines:
            logger.warning(f"Не удалось получить свечи для проверки сигнала для бота {self.bot.id}")
            return False

        highs = [safe_float(kline[2]) for kline in klines]
        lows = [safe_float(kline[3]) for kline in klines]
        closes = [safe_float(kline[4]) for kline in klines]
        volumes = [safe_float(kline[5]) for kline in klines]

        if signal_type == 'rsi':
            period = signal_params.get('period', 14)
            threshold = signal_params.get('threshold', 30)
            rsi = calculate_rsi(tuple(closes), period)
            if rsi is None:
                logger.warning(f"RSI не рассчитан для бота {self.bot.id}, недостаточно данных")
                return False
            logger.debug(f"RSI: {rsi}, Порог: {threshold}, Интервал: {self.signal_interval}")
            return rsi < threshold
        elif signal_type == 'cci':
            period = signal_params.get('period', 20)
            threshold = signal_params.get('threshold', -100)
            cci = calculate_cci(tuple(highs), tuple(lows), tuple(closes), period)
            if cci is None:
                logger.warning(f"CCI не рассчитан для бота {self.bot.id}, недостаточно данных")
                return False
            logger.debug(f"CCI: {cci}, Порог: {threshold}")
            return cci < threshold
        elif signal_type == 'mfi':
            period = signal_params.get('period', 14)
            threshold = signal_params.get('threshold', 20)
            mfi = calculate_mfi(tuple(highs), tuple(lows), tuple(closes), tuple(volumes), period)
            if mfi is None:
                logger.warning(f"MFI не рассчитан для бота {self.bot.id}, недостаточно данных")
                return False
            logger.debug(f"MFI: {mfi}, Порог: {threshold}")
            return mfi < threshold
        elif signal_type == 'macd':
            fast_period = signal_params.get('fast_period', 12)
            slow_period = signal_params.get('slow_period', 26)
            signal_period = signal_params.get('signal_period', 9)
            condition = signal_params.get('condition', 'crossover')
            macd_line, signal_line, _ = calculate_macd(tuple(closes), fast_period, slow_period, signal_period)
            if macd_line is None or signal_line is None:
                logger.warning(f"MACD не рассчитан для бота {self.bot.id}, недостаточно данных")
                return False
            prev_macd_line, prev_signal_line, _ = calculate_macd(tuple(closes[:-1]), fast_period, slow_period, signal_period)
            if prev_macd_line is None or prev_signal_line is None:
                logger.warning(f"Недостаточно данных для проверки MACD crossover для бота {self.bot.id}")
                return False
            logger.debug(f"MACD: {macd_line}, Signal: {signal_line}, Prev MACD: {prev_macd_line}, Prev Signal: {prev_signal_line}")
            if condition == 'crossover':
                return macd_line > signal_line and prev_macd_line <= prev_signal_line
            elif condition == 'crossunder':
                return macd_line < signal_line and prev_macd_line >= prev_signal_line
            return False
        elif signal_type == 'bollinger_bands':
            period = signal_params.get('period', 20)
            dev = signal_params.get('dev', 2)
            upper, lower, _ = calculate_bollinger_bands(tuple(closes), period, dev)
            if upper is None or lower is None:
                logger.warning(f"Bollinger Bands не рассчитаны для бота {self.bot.id}, недостаточно данных")
                return False
            logger.debug(f"Bollinger Bands: Upper={upper}, Lower={lower}, Price={closes[-1]}")
            return closes[-1] < lower
        elif signal_type == 'stochastic':
            k_period = signal_params.get('k_period', 14)
            d_period = signal_params.get('d_period', 3)
            threshold = signal_params.get('threshold', 20)
            k, _ = calculate_stochastic(tuple(highs), tuple(lows), tuple(closes), k_period, d_period)
            if k is None:
                logger.warning(f"Stochastic не рассчитан для бота {self.bot.id}, недостаточно данных")
                return False
            logger.debug(f"Stochastic %K: {k}, Порог: {threshold}")
            return k < threshold
        elif signal_type == 'price':
            current_price = self.get_current_price()
            target_price = signal_params.get('target_price')
            logger.debug(f"Current price: {current_price}, Target price: {target_price}")
            return current_price <= target_price if current_price and target_price else False
        elif signal_type == 'volume_spike':
            lookback = signal_params.get('lookback', 10)
            threshold = signal_params.get('threshold', 2)
            current_volume, avg_volume = calculate_volume_spike(tuple(volumes), lookback)
            if current_volume is None or avg_volume is None:
                logger.warning(f"Volume Spike не рассчитан для бота {self.bot.id}, недостаточно данных")
                return False
            logger.debug(f"Volume Spike: Current={current_volume}, Avg={avg_volume}, Порог={threshold}")
            return current_volume > avg_volume * threshold
        elif signal_type == 'ma_crossover':
            short_period = signal_params.get('short_period', 10)
            long_period = signal_params.get('long_period', 20)
            short_ma, long_ma, prev_short_ma, prev_long_ma = calculate_ma_crossover(
                tuple(closes), short_period, long_period, ma_type=self.settings.ma_crossover_type
            )
            if any(v is None for v in [short_ma, long_ma, prev_short_ma, prev_long_ma]):
                logger.warning(f"MA Crossover не рассчитан для бота {self.bot.id}, недостаточно данных")
                return False
            logger.debug(f"MA Crossover: Short={short_ma}, Long={long_ma}, Prev Short={prev_short_ma}, Prev Long={prev_long_ma}")
            return short_ma > long_ma and prev_short_ma <= prev_long_ma
        elif signal_type == 'pivot_points':
            pivot, r1, s1 = calculate_pivot_points(
                tuple(highs), tuple(lows), tuple(closes), period=self.settings.pivot_points_period
            )
            if any(v is None for v in [pivot, r1, s1]):
                logger.warning(f"Pivot Points не рассчитаны для бота {self.bot.id}, недостаточно данных")
                return False
            current_price = closes[-1]
            condition = signal_params.get('condition', 'above_resistance')
            logger.debug(f"Pivot Points: Pivot={pivot}, R1={r1}, S1={s1}, Price={current_price}, Condition={condition}")
            return current_price > r1 if condition == 'above_resistance' else current_price < s1
        elif signal_type == 'adx':
            period = signal_params.get('period', 14)
            threshold = signal_params.get('threshold', 25)
            adx = calculate_adx(tuple(highs), tuple(lows), tuple(closes), period)
            if adx is None:
                logger.warning(f"ADX не рассчитан для бота {self.bot.id}, недостаточно данных")
                return False
            logger.debug(f"ADX: {adx}, Порог: {threshold}")
            return adx > threshold
        elif signal_type == 'atr':
            period = signal_params.get('period', 14)
            threshold = signal_params.get('threshold', 1.0)
            atr = calculate_atr(tuple(highs), tuple(lows), tuple(closes), period)
            if atr is None:
                logger.warning(f"ATR не рассчитан для бота {self.bot.id}, недостаточно данных")
                return False
            logger.debug(f"ATR: {atr}, Порог: {threshold}")
            return atr > threshold
        elif signal_type == 'ichimoku':
            tenkan_period = signal_params.get('tenkan_period', 9)
            kijun_period = signal_params.get('kijun_period', 26)
            senkou_period = signal_params.get('senkou_period', 52)
            condition = signal_params.get('condition', 'above_cloud')
            senkou_a, senkou_b, kijun, tenkan = calculate_ichimoku(
                tuple(highs), tuple(lows), tuple(closes), tenkan_period, kijun_period, senkou_period
            )
            if any(v is None for v in [senkou_a, senkou_b]):
                logger.warning(f"Ichimoku не рассчитан для бота {self.bot.id}, недостаточно данных")
                return False
            current_price = closes[-1]
            cloud_top = max(senkou_a, senkou_b)
            cloud_bottom = min(senkou_a, senkou_b)
            logger.debug(f"Ichimoku: Senkou A={senkou_a}, Senkou B={senkou_b}, Price={current_price}, Condition={condition}")
            if condition == 'above_cloud':
                return current_price > cloud_top
            elif condition == 'below_cloud':
                return current_price < cloud_bottom
            return False
        else:
            logger.warning(f"Неизвестный тип сигнала для бота {self.bot.id}: {signal_type}")
            return False

    def get_klines(self, interval, limit=100):
        """
        Получает исторические свечи для расчёта индикаторов, с поддержкой разных бирж и категорий.

        Args:
            interval (str): Интервал свечей (например, '1h', '1d').
            limit (int): Количество свечей.

        Returns:
            list: Список свечей или None в случае ошибки.
        """
        cache_key = f"klines_{self.bot.trading_pair}_{interval}_{limit}_{self.category}"
        klines = cache.get(cache_key)
        if klines is not None:
            logger.debug(f"Свечи для {self.bot.trading_pair} извлечены из кэша для бота {self.bot.id}")
        else:
            trading_pair = self.bot.trading_pair.replace('/', '') if self.bot.trading_pair else ''
            try:
                klines = ExchangeAPI.get_klines(
                    self.exchange, trading_pair, interval, limit, category=self.category
                )
                cache.set(cache_key, klines, timeout=settings.KLINES_CACHE_TIMEOUT)
                logger.debug(f"Свечи для {trading_pair} закэшированы на {settings.KLINES_CACHE_TIMEOUT} секунд")
            except Exception as e:
                logger.error(f"Ошибка получения свечей для {self.exchange} для бота {self.bot.id}: {str(e)}")
                return None
        return klines

    def get_current_price(self, category=None):
        """
        Получает текущую рыночную цену для торговой пары.

        Args:
            category (str, optional): Категория ('spot' или 'linear'). По умолчанию берётся из self.category.

        Returns:
            float: Текущая цена или None в случае ошибки.
        """
        category = category or self.category
        trading_pair = self.bot.trading_pair.replace('/', '') if self.bot.trading_pair else ''
        if not trading_pair:
            logger.error(f"Торговая пара не указана для бота {self.bot.id}")
            return None
        cache_key = f"price_{self.exchange}_{trading_pair}_{category}"
        price = cache.get(cache_key)
        if price is not None:
            logger.debug(f"Текущая цена для {trading_pair} ({category}) извлечена из кэша: {price}")
            return price

        try:
            if self.exchange == 'bybit':
                url = f"https://api.bybit.com/v5/market/tickers?category={category}&symbol={trading_pair}"
                response = requests.get(url, timeout=10)
                response.raise_for_status()
                data = response.json()
                if data['retCode'] == 0:
                    price = safe_float(data['result']['list'][0]['lastPrice'])
                else:
                    logger.error(f"Ошибка получения цены на Bybit для {trading_pair}: {data['retMsg']}")
                    return None
            elif self.exchange == 'binance':
                url = "https://api.binance.com/api/v3/ticker/price" if category == 'spot' else "https://fapi.binance.com/fapi/v1/ticker/price"
                params = {"symbol": trading_pair}
                response = requests.get(url, params=params, timeout=10)
                response.raise_for_status()
                data = response.json()
                price = safe_float(data['price'])
            elif self.exchange == 'okx':
                url = f"https://www.okx.com/api/v5/market/ticker?instId={trading_pair.replace('/', '-')}"
                response = requests.get(url, timeout=10)
                response.raise_for_status()
                data = response.json()
                if data['code'] == '0':
                    price = safe_float(data['data'][0]['last'])
                else:
                    logger.error(f"Ошибка получения цены на OKX для {trading_pair}: {data['msg']}")
                    return None
            else:
                logger.error(f"Биржа {self.exchange} не поддерживается для получения цены")
                return None

            if price:
                cache.set(cache_key, price, timeout=settings.PRICE_CACHE_TIMEOUT)
                logger.debug(f"Текущая цена для {trading_pair} ({category}): {price}, закэширована на {settings.PRICE_CACHE_TIMEOUT} секунд")
            return price
        except requests.RequestException as e:
            logger.error(f"Ошибка запроса цены для {trading_pair} на {self.exchange}: {str(e)}")
            return None

    def get_price_precision(self):
        """
        Получает точность цены для торговой пары.

        Returns:
            float: Значение tick size.
        """
        trading_pair = self.bot.trading_pair.replace('/', '') if self.bot.trading_pair else ''
        cache_key = f"tick_size_{self.exchange}_{trading_pair}_{self.category}"
        tick_size = cache.get(cache_key)
        if tick_size is not None:
            logger.debug(f"Tick size для {trading_pair} извлечён из кэша: {tick_size}")
            return tick_size

        try:
            if self.exchange == 'bybit':
                url = f"https://api.bybit.com/v5/market/instruments-info?category={self.category}&symbol={trading_pair}"
                response = requests.get(url, timeout=10)
                response.raise_for_status()
                data = response.json()
                if data['retCode'] == 0:
                    tick_size = safe_float(data['result']['list'][0]['priceFilter']['tickSize'])
                else:
                    logger.error(f"Ошибка получения tick size на Bybit для {trading_pair}: {data['retMsg']}")
                    return 0.0001
            elif self.exchange == 'binance':
                url = "https://api.binance.com/api/v3/exchangeInfo" if self.category == 'spot' else "https://fapi.binance.com/fapi/v1/exchangeInfo"
                response = requests.get(url, timeout=10)
                response.raise_for_status()
                data = response.json()
                for symbol in data['symbols']:
                    if symbol['symbol'] == trading_pair:
                        for filt in symbol['filters']:
                            if filt['filterType'] == 'PRICE_FILTER':
                                tick_size = safe_float(filt['tickSize'])
                                break
                        break
                else:
                    logger.error(f"Торговая пара {trading_pair} не найдена на Binance")
                    return 0.0001
            elif self.exchange == 'okx':
                url = f"https://www.okx.com/api/v5/public/instruments?instType={'SPOT' if self.category == 'spot' else 'SWAP'}"
                response = requests.get(url, timeout=10)
                response.raise_for_status()
                data = response.json()
                if data['code'] == '0':
                    for instrument in data['data']:
                        if instrument['instId'] == trading_pair.replace('/', '-'):
                            tick_size = safe_float(instrument['tickSz'])
                            break
                    else:
                        logger.error(f"Торговая пара {trading_pair} не найдена на OKX")
                        return 0.0001
                else:
                    logger.error(f"Ошибка получения tick size на OKX для {trading_pair}: {data['msg']}")
                    return 0.0001
            else:
                logger.error(f"Биржа {self.exchange} не поддерживается для получения tick size")
                return 0.0001

            cache.set(cache_key, tick_size, timeout=3600)
            logger.debug(f"Tick size для {trading_pair} закэширован: {tick_size}")
            return tick_size
        except requests.RequestException as e:
            logger.error(f"Ошибка запроса tick size для {trading_pair} на {self.exchange}: {str(e)}")
            return 0.0001

    def get_min_order_size(self):
        """
        Получает минимальный размер ордера для торговой пары.

        Returns:
            float: Минимальный размер ордера.
        """
        trading_pair = self.bot.trading_pair.replace('/', '') if self.bot.trading_pair else ''
        cache_key = f"min_order_size_{self.exchange}_{trading_pair}_{self.category}"
        min_order_size = cache.get(cache_key)
        if min_order_size is not None:
            logger.debug(f"Минимальный размер ордера для {trading_pair} извлечён из кэша: {min_order_size}")
            return min_order_size

        try:
            if self.exchange == 'bybit':
                url = f"https://api.bybit.com/v5/market/instruments-info?category={self.category}&symbol={trading_pair}"
                response = requests.get(url, timeout=10)
                response.raise_for_status()
                data = response.json()
                if data['retCode'] == 0:
                    min_order_size = safe_float(data['result']['list'][0]['lotSizeFilter']['minOrderQty'])
                else:
                    logger.error(f"Ошибка получения min order size на Bybit для {trading_pair}: {data['retMsg']}")
                    return 0.001
            elif self.exchange == 'binance':
                url = "https://api.binance.com/api/v3/exchangeInfo" if self.category == 'spot' else "https://fapi.binance.com/fapi/v1/exchangeInfo"
                response = requests.get(url, timeout=10)
                response.raise_for_status()
                data = response.json()
                for symbol in data['symbols']:
                    if symbol['symbol'] == trading_pair:
                        for filt in symbol['filters']:
                            if filt['filterType'] == 'LOT_SIZE':
                                min_order_size = safe_float(filt['minQty'])
                                break
                        break
                else:
                    logger.error(f"Торговая пара {trading_pair} не найдена на Binance")
                    return 0.001
            elif self.exchange == 'okx':
                url = f"https://www.okx.com/api/v5/public/instruments?instType={'SPOT' if self.category == 'spot' else 'SWAP'}"
                response = requests.get(url, timeout=10)
                response.raise_for_status()
                data = response.json()
                if data['code'] == '0':
                    for instrument in data['data']:
                        if instrument['instId'] == trading_pair.replace('/', '-'):
                            min_order_size = safe_float(instrument['minSz'])
                            break
                    else:
                        logger.error(f"Торговая пара {trading_pair} не найдена на OKX")
                        return 0.001
                else:
                    logger.error(f"Ошибка получения min order size на OKX для {trading_pair}: {data['msg']}")
                    return 0.001
            else:
                logger.error(f"Биржа {self.exchange} не поддерживается для получения min order size")
                return 0.001

            cache.set(cache_key, min_order_size, timeout=3600)
            logger.debug(f"Минимальный размер ордера для {trading_pair} закэширован: {min_order_size}")
            return min_order_size
        except requests.RequestException as e:
            logger.error(f"Ошибка запроса min order size для {trading_pair} на {self.exchange}: {str(e)}")
            return 0.001

    def round_price(self, price, tick_size):
        """
        Округляет цену до ближайшего значения, кратного tick_size.

        Args:
            price (float): Цена для округления.
            tick_size (float): Размер шага цены.

        Returns:
            float: Округлённая цена.
        """
        return math.floor(price / tick_size) * tick_size

    def place_order(self, side, price, qty, category=None):
        """
        Размещает ордер на бирже.

        Args:
            side (str): Сторона ордера ('buy' или 'sell').
            price (float): Цена ордера.
            qty (float): Количество.
            category (str, optional): Категория ('spot' или 'linear'). По умолчанию берётся из self.category.

        Returns:
            dict: Результат выполнения ордера.
        """
        category = category or self.category
        trading_pair = self.bot.trading_pair.replace('/', '') if self.bot.trading_pair else ''
        precision = self.get_price_precision()
        formatted_price = self.round_price(price, precision)
        try:
            if self.exchange == 'bybit':
                # Подготовка параметров для Bybit с учетом фьючерсов
                order_params = {
                    "category": category,
                    "symbol": trading_pair,
                    "side": side.capitalize(),
                    "orderType": "Limit",
                    "qty": str(qty),
                    "price": str(formatted_price),
                    "timeInForce": "GTC",
                }
                if category == 'linear':
                    order_params["leverage"] = str(self.bot.leverage) if self.bot.leverage else "1"
                    order_params["marginType"] = self.bot.margin_type if self.bot.margin_type else "isolated"
                # Используем ExchangeAPI для размещения ордера
                result = ExchangeAPI.create_order(
                    exchange=self.exchange,
                    api_key=self.api_key,
                    api_secret=self.api_secret,
                    symbol=trading_pair,
                    side=side,
                    qty=qty,
                    price=formatted_price,
                    category=category,
                    leverage=self.bot.leverage if category == 'linear' else None,
                    margin_type=self.bot.margin_type if category == 'linear' else None,
                    additional_params={"timeInForce": "GTC"}
                )
            else:
                result = ExchangeAPI.create_order(
                    exchange=self.exchange,
                    api_key=self.api_key,
                    api_secret=self.api_secret,
                    symbol=trading_pair,
                    side=side,
                    qty=qty,
                    price=formatted_price,
                    category=category,
                    leverage=self.bot.leverage if category == 'linear' else None,
                    margin_type=self.bot.margin_type if category == 'linear' else None
                )
            logger.info(f"Размещён ордер: bot_id={self.bot.id}, side={side}, price={formatted_price}, qty={qty}, order_id={result['orderId']}")
            return result
        except Exception as e:
            logger.error(f"Ошибка при размещении ордера для бота {self.bot.id} (side={side}, price={formatted_price}, qty={qty}): {str(e)}")
            raise

    def run_advanced_grid(self):
        """
        Реализует стратегию сетки ордеров с учётом сигналов и тейк-профита.
        """
        logger.info("Запуск стратегии order_grid для бота %s, trading_pair=%s", self.bot.id, self.bot.trading_pair)
        try:
            if not self.check_signal():
                logger.info(f"Сигнал не сработал для бота {self.bot.id}, ожидаем")
                return

            current_price = self.get_current_price()
            if not current_price:
                raise ValueError(f"Cannot fetch current price for {self.bot.trading_pair} on {self.exchange}")

            self.check_open_orders()

            if self.position > 0 and not self.sell_order_id:
                self.place_sell_order()

            if not self.position_opened and len(self.buy_orders) < self.grid_orders:
                buy_levels = self.calculate_buy_levels(current_price)
                min_order_size = self.get_min_order_size()
                for i, buy_price in enumerate(buy_levels[:self.grid_orders - len(self.buy_orders)]):
                    qty = self.calculate_quantity(i)
                    if qty < min_order_size:
                        logger.warning(f"Объём {qty} меньше минимального {min_order_size} для бота {self.bot.id}, пропускаем ордер")
                        continue
                    try:
                        result = self.place_order('buy', buy_price, qty)
                        order_id = result['orderId']
                        self.buy_orders.append(order_id)
                        self.position_obj.buy_orders = self.buy_orders
                        self.position_obj.save()
                        logger.info(f"Размещён ордер на покупку: bot_id={self.bot.id}, price={buy_price}, qty={qty}, order_id={order_id}")
                    except Exception as e:
                        logger.error(f"Ошибка при размещении ордера покупки для бота {self.bot.id}: {str(e)}")
                        continue

            if self.grid_follow and self.position_opened:
                self.adjust_grid(current_price)

        except Exception as e:
            logger.error(f"Ошибка в run_advanced_grid для бота {self.bot.id}: {str(e)}", exc_info=True)
            raise

    def run_martingale(self):
        """
        Реализует стратегию мартингейла.
        """
        logger.info("Запуск стратегии martingale для бота %s", self.bot.id)
        try:
            if not self.check_signal():
                logger.info("Сигнал не сработал для бота %s, ожидаем", self.bot.id)
                return
            current_price = self.get_current_price()
            if not current_price:
                raise ValueError(f"Cannot fetch current price for {self.bot.trading_pair} on {self.exchange}")
            base_qty = safe_float(self.bot.additional_settings.get('base_quantity', 0.1))
            qty = base_qty * (1 + self.martingale) ** len(self.buy_orders)
            min_order_size = self.get_min_order_size()
            if qty < min_order_size:
                logger.warning(f"Объём {qty} меньше минимального {min_order_size} для бота {self.bot.id}, увеличиваем до минимального")
                qty = min_order_size
            precision = self.get_price_precision()
            formatted_price = self.round_price(current_price, precision)
            result = self.place_order('buy', formatted_price, qty)
            self.update_position(formatted_price, qty)
            self.buy_orders.append(result['orderId'])
            self.position_obj.buy_orders = self.buy_orders
            self.position_obj.save()
            logger.info("Мартингейл: размещён ордер на покупку: bot_id=%s, price=%s, qty=%s", self.bot.id, formatted_price, qty)
        except Exception as e:
            logger.error("Ошибка в run_martingale для бота %s: %s", self.bot.id, str(e), exc_info=True)
            raise

    def run_dca(self):
        """
        Реализует стратегию Dollar-Cost Averaging (DCA).
        """
        logger.info("Запуск стратегии DCA для бота %s", self.bot.id)
        try:
            if not self.check_signal():
                logger.info("Сигнал не сработал для бота %s, ожидаем", self.bot.id)
                return
            current_price = self.get_current_price()
            if not current_price:
                raise ValueError(f"Cannot fetch current price for {self.bot.trading_pair} on {self.exchange}")

            cache_key = f"dca_last_execution_{self.bot.id}"
            last_execution = cache.get(cache_key)
            if last_execution and (time.time() - last_execution) < (self.dca_interval * 60):
                logger.info(f"DCA: слишком рано для нового ордера, bot_id={self.bot.id}")
                return

            base_qty = safe_float(self.bot.additional_settings.get('base_quantity', 0.1))
            min_order_size = self.get_min_order_size()
            if base_qty < min_order_size:
                logger.warning(f"Объём {base_qty} меньше минимального {min_order_size} для бота {self.bot.id}, увеличиваем до минимального")
                base_qty = min_order_size
            precision = self.get_price_precision()
            formatted_price = self.round_price(current_price, precision)
            result = self.place_order('buy', formatted_price, base_qty)
            self.update_position(formatted_price, base_qty)
            self.buy_orders.append(result['orderId'])
            self.position_obj.buy_orders = self.buy_orders
            self.position_obj.save()
            cache.set(cache_key, time.time(), timeout=self.dca_interval * 60)
            logger.info("DCA: размещён ордер на покупку: bot_id=%s, price=%s, qty=%s", self.bot.id, formatted_price, base_qty)
        except Exception as e:
            logger.error("Ошибка в run_dca для бота %s: %s", self.bot.id, str(e), exc_info=True)
            raise

    def run_trailing_stop(self):
        """
        Реализует стратегию Trailing Stop.
        """
        logger.info("Запуск стратегии trailing_stop для бота %s", self.bot.id)
        try:
            current_price = self.get_current_price()
            if not current_price:
                raise ValueError(f"Cannot fetch current price for {self.bot.trading_pair} on {self.exchange}")

            # Открываем позицию, если её нет и сигнал сработал
            if not self.position_opened or self.position <= 0:
                if self.check_signal():
                    base_qty = safe_float(self.bot.additional_settings.get('base_quantity', 0.1))
                    min_order_size = self.get_min_order_size()
                    if base_qty < min_order_size:
                        logger.warning(f"Объём {base_qty} меньше минимального {min_order_size} для бота {self.bot.id}, увеличиваем до минимального")
                        base_qty = min_order_size
                    result = self.place_order('buy', current_price, base_qty)
                    self.update_position(current_price, base_qty)
                    self.highest_price = current_price
                    self.position_opened = True
                    self.buy_orders.append(result['orderId'])
                    self.position_obj.highest_price = self.highest_price
                    self.position_obj.position_opened = self.position_opened
                    self.position_obj.buy_orders = self.buy_orders
                    self.position_obj.save()
                    logger.info(f"Trailing Stop: открыта позиция: bot_id={self.bot.id}, price={current_price}, qty={base_qty}")
                return

            # Проверяем trailing stop
            if self.position > 0:
                trailing_percentage = self.trailing_stop_percentage or 0.01  # 1% по умолчанию, если не указано
                highest_price = self.highest_price or self.avg_price
                self.highest_price = max(highest_price, current_price)
                self.position_obj.highest_price = self.highest_price
                stop_price = self.highest_price * (1 - trailing_percentage)
                if current_price <= stop_price:
                    result = self.place_order('sell', current_price, self.position)
                    profit = (current_price - self.avg_price) * self.position
                    self.close_position(profit=profit)
                    logger.info(f"Trailing Stop сработал: позиция закрыта: bot_id={self.bot.id}, price={current_price}, profit={profit}")
                else:
                    self.position_obj.save()
                    logger.debug(f"Trailing Stop: текущая цена={current_price}, stop_price={stop_price}, highest_price={self.highest_price}")

        except Exception as e:
            logger.error("Ошибка в run_trailing_stop для бота %s: %s", self.bot.id, str(e), exc_info=True)
            raise

    def run_arbitrage(self):
        """
        Реализует стратегию арбитража между спотом и фьючерсами.
        """
        logger.info("Запуск стратегии arbitrage для бота %s", self.bot.id)
        try:
            spot_price = self.get_current_price(category='spot')
            futures_price = self.get_current_price(category='linear')
            if not spot_price or not futures_price:
                raise ValueError(f"Cannot fetch prices for {self.bot.trading_pair} on {self.exchange}")

            spread = (futures_price - spot_price) / spot_price
            threshold = self.settings.arbitrage_spread_threshold / 100 if self.settings.arbitrage_spread_threshold else 0.005  # 0.5% по умолчанию
            base_qty = safe_float(self.bot.additional_settings.get('base_quantity', 0.1))
            min_order_size = self.get_min_order_size()
            if base_qty < min_order_size:
                logger.warning(f"Объём {base_qty} меньше минимального {min_order_size} для бота {self.bot.id}, увеличиваем до минимального")
                base_qty = min_order_size

            if spread > threshold:
                # Покупаем на споте, продаём на фьючерсах
                spot_result = self.place_order('buy', spot_price, base_qty, category='spot')
                futures_result = self.place_order('sell', futures_price, base_qty, category='linear')
                self.update_position(spot_price, base_qty)
                self.buy_orders.append(spot_result['orderId'])
                self.position_obj.buy_orders = self.buy_orders
                self.position_obj.save()
                logger.info(f"Arbitrage: buy spot at {spot_price}, sell futures at {futures_price}, bot_id={self.bot.id}")
            elif spread < -threshold:
                # Продаём на споте, покупаем на фьючерсах
                spot_result = self.place_order('sell', spot_price, base_qty, category='spot')
                futures_result = self.place_order('buy', futures_price, base_qty, category='linear')
                self.update_position(futures_price, base_qty)
                self.buy_orders.append(futures_result['orderId'])
                self.position_obj.buy_orders = self.buy_orders
                self.position_obj.save()
                logger.info(f"Arbitrage: sell spot at {spot_price}, buy futures at {futures_price}, bot_id={self.bot.id}")
            else:
                logger.info(f"Arbitrage: спред {spread:.4f} ниже порога {threshold}, bot_id={self.bot.id}")
        except Exception as e:
            logger.error("Ошибка в run_arbitrage для бота %s: %s", self.bot.id, str(e), exc_info=True)
            raise

    def run_custom(self):
        """
        Заглушка для пользовательской стратегии.
        """
        logger.warning(f"Custom strategy is not implemented for bot {self.bot.id}")
        raise NotImplementedError("Custom strategy is not implemented yet.")

    def check_stop_loss(self):
        """
        Проверяет, достигнут ли стоп-лосс, и закрывает позицию, если необходимо.
        """
        if not self.stop_loss:
            return
        current_price = self.get_current_price()
        if not current_price or not self.settings.stop_loss:
            logger.error(f"Cannot fetch current price for {self.bot.trading_pair} on {self.exchange} для бота {self.bot.id}")
            return

        stop_loss_price = self.avg_price * (1 - self.stop_loss)
        if current_price <= stop_loss_price:
            try:
                result = self.place_order('sell', current_price, self.position)
                loss = (current_price - self.avg_price) * self.position
                self.close_position(profit=loss)
                logger.info(f"Stop Loss сработал: позиция закрыта: bot_id={self.bot.id}, price={current_price}, loss={loss}")
            except Exception as e:
                logger.error(f"Ошибка при срабатывании Stop Loss для бота {self.bot.id}: {str(e)}")
                raise

    def place_sell_order(self):
        """
        Размещает ордер на продажу с учётом тейк-профита.
        """
        if self.position <= 0 or self.sell_order_id:
            return
        try:
            current_price = self.get_current_price()
            if not current_price:
                raise ValueError(f"Cannot fetch current price for {self.bot.trading_pair} on {self.exchange}")
            sell_price = self.avg_price * (1 + self.take_profit)
            precision = self.get_price_precision()
            formatted_price = self.round_price(sell_price, precision)
            result = self.place_order('sell', formatted_price, self.position)
            self.sell_order_id = result['orderId']
            self.position_obj.sell_order_id = self.sell_order_id
            self.position_obj.save()
            logger.info(f"Размещён ордер на продажу: bot_id={self.bot.id}, price={formatted_price}, qty={self.position}")
        except Exception as e:
            logger.error(f"Ошибка при размещении ордера продажи для бота {self.bot.id}: {str(e)}")
            raise

    def check_open_orders(self):
        """
        Проверяет состояние открытых ордеров и обновляет позицию.
        """
        trading_pair = self.bot.trading_pair.replace('/', '') if self.bot.trading_pair else ''
        if not trading_pair:
            logger.error(f"Торговая пара не указана для бота {self.bot.id}")
            return

        try:
            if self.exchange == 'bybit':
                url = "https://api.bybit.com/v5/order/realtime"
                timestamp = str(get_bybit_server_time())
                params = {"category": self.category, "symbol": trading_pair}
                query_string = urlencode(sorted(params.items()))
                sign_str = timestamp + self.api_key + str(self.recv_window) + query_string
                signature = hmac.new(self.api_secret.encode('utf-8'), sign_str.encode('utf-8'), hashlib.sha256).hexdigest()
                headers = {
                    "X-BAPI-API-KEY": self.api_key,
                    "X-BAPI-TIMESTAMP": timestamp,
                    "X-BAPI-RECV-WINDOW": str(self.recv_window),
                    "X-BAPI-SIGN": signature,
                }
                response = requests.get(url, headers=headers, params=params, timeout=10)
                response.raise_for_status()
                data = response.json()
                if data['retCode'] == 0:
                    open_orders = data['result']['list']
                    remaining_buy_orders = []
                    for order in open_orders:
                        order_id = order['orderId']
                        if order_id in self.buy_orders:
                            if order['orderStatus'] == 'Filled':
                                price = safe_float(order['price'])
                                qty = safe_float(order['qty'])
                                self.update_position(price, qty)
                                self.bot.deals_completed += 1
                                self.bot.save()
                                logger.info("Ордер покупки исполнен: bot_id=%s, price=%s, qty=%s", self.bot.id, price, qty)
                            else:
                                remaining_buy_orders.append(order_id)
                        elif order_id == self.sell_order_id and order['orderStatus'] == 'Filled':
                            profit = (safe_float(order['price']) - self.avg_price) * self.position
                            self.close_position(profit=profit)
                            self.bot.deals_completed += 1
                            self.bot.save()
                            logger.info("Ордер продажи исполнен, позиция закрыта: bot_id=%s, прибыль=%s", self.bot.id, profit)
                    self.buy_orders = remaining_buy_orders
                    self.position_obj.buy_orders = self.buy_orders
                    self.position_obj.save()
                else:
                    logger.error("Ошибка проверки открытых ордеров на Bybit для бота %s: %s", self.bot.id, data['retMsg'])
            elif self.exchange == 'binance':
                url = "https://api.binance.com/api/v3/openOrders" if self.category == 'spot' else "https://fapi.binance.com/fapi/v1/openOrders"
                timestamp = str(int(time.time() * 1000))
                params = {"symbol": trading_pair, "timestamp": timestamp}
                query_string = urlencode(sorted(params.items()))
                signature = hmac.new(self.api_secret.encode('utf-8'), query_string.encode('utf-8'), hashlib.sha256).hexdigest()
                params["signature"] = signature
                headers = {"X-MBX-APIKEY": self.api_key}
                response = requests.get(url, headers=headers, params=params, timeout=10)
                response.raise_for_status()
                open_orders = response.json()
                remaining_buy_orders = []
                for order in open_orders:
                    order_id = str(order['orderId'])
                    if order_id in self.buy_orders:
                        if order['status'] == 'FILLED':
                            price = safe_float(order['price'])
                            qty = safe_float(order['origQty'])
                            self.update_position(price, qty)
                            self.bot.deals_completed += 1
                            self.bot.save()
                            logger.info("Ордер покупки исполнен: bot_id=%s, price=%s, qty=%s", self.bot.id, price, qty)
                        else:
                            remaining_buy_orders.append(order_id)
                    elif order_id == self.sell_order_id and order['status'] == 'FILLED':
                        profit = (safe_float(order['price']) - self.avg_price) * self.position
                        self.close_position(profit=profit)
                        self.bot.deals_completed += 1
                        self.bot.save()
                        logger.info("Ордер продажи исполнен, позиция закрыта: bot_id=%s, прибыль=%s", self.bot.id, profit)
                self.buy_orders = remaining_buy_orders
                self.position_obj.buy_orders = self.buy_orders
                self.position_obj.save()
            elif self.exchange == 'okx':
                url = "https://www.okx.com/api/v5/trade/orders-pending"
                timestamp = str(int(time.time()))
                method = "GET"
                request_path = "/api/v5/trade/orders-pending"
                body = ""
                sign_str = timestamp + method + request_path + body
                signature = hmac.new(self.api_secret.encode('utf-8'), sign_str.encode('utf-8'), hashlib.sha256).hexdigest()
                headers = {
                    "OK-ACCESS-KEY": self.api_key,
                    "OK-ACCESS-SIGN": signature,
                    "OK-ACCESS-TIMESTAMP": timestamp,
                    "OK-ACCESS-PASSPHRASE": ""
                }
                response = requests.get(url, headers=headers, timeout=10)
                response.raise_for_status()
                data = response.json()
                if data['code'] == '0':
                    open_orders = [order for order in data['data'] if order['instId'] == trading_pair.replace('/', '-')]
                    remaining_buy_orders = []
                    for order in open_orders:
                        order_id = order['ordId']
                        if order_id in self.buy_orders:
                            if order['state'] == 'filled':
                                price = safe_float(order['px'])
                                qty = safe_float(order['sz'])
                                self.update_position(price, qty)
                                self.bot.deals_completed += 1
                                self.bot.save()
                                logger.info("Ордер покупки исполнен: bot_id=%s, price=%s, qty=%s", self.bot.id, price, qty)
                            else:
                                remaining_buy_orders.append(order_id)
                        elif order_id == self.sell_order_id and order['state'] == 'filled':
                            profit = (safe_float(order['px']) - self.avg_price) * self.position
                            self.close_position(profit=profit)
                            self.bot.deals_completed += 1
                            self.bot.save()
                            logger.info("Ордер продажи исполнен, позиция закрыта: bot_id=%s, прибыль=%s", self.bot.id, profit)
                    self.buy_orders = remaining_buy_orders
                    self.position_obj.buy_orders = self.buy_orders
                    self.position_obj.save()
                else:
                    logger.error(f"Ошибка проверки открытых ордеров на OKX для бота {self.bot.id}: {data['msg']}")
            else:
                logger.error(f"Биржа {self.exchange} не поддерживается для бота {self.bot.id}")
                raise NotImplementedError(f"Exchange {self.exchange} not supported")
        except requests.RequestException as e:
            logger.error(f"Ошибка при проверке открытых ордеров для бота {self.bot.id}: {str(e)}")

    def calculate_buy_levels(self, current_price):
        """
        Рассчитывает уровни покупки для сетки.

        Args:
            current_price (float): Текущая цена.

        Returns:
            list: Список цен для размещения ордеров на покупку.
        """
        buy_levels = []
        step = current_price * self.grid_spacing * (1 + self.grid_overlap)
        for i in range(self.grid_orders):
            if self.logarithmic:
                price = current_price * (1 - self.grid_spacing * (i + 1) ** 1.2)
            else:
                price = current_price - step * (i + 1)
            price = self.round_price(price, self.get_price_precision())
            buy_levels.append(price)
        return buy_levels

    def calculate_quantity(self, level_index):
        """
        Рассчитывает объём ордера с учётом мартингейла и минимального размера.

        Args:
            level_index (int): Индекс уровня в сетке.

        Returns:
            float: Объём ордера.
        """
        base_qty = safe_float(self.bot.additional_settings.get('base_quantity', 0.1))
        min_order_size = self.get_min_order_size()
        qty = base_qty * (1 + self.martingale) ** level_index
        if qty < min_order_size:
            logger.warning(f"Объём {qty} меньше минимального {min_order_size} для бота {self.bot.id}, увеличиваем до минимального")
            qty = min_order_size

        # Округляем до base_precision
        trading_pair = self.bot.trading_pair.replace('/', '') if self.bot.trading_pair else ''
        cache_key = f"base_precision_{self.exchange}_{trading_pair}_{self.category}"
        base_precision = cache.get(cache_key)
        if base_precision is None:
            try:
                if self.exchange == 'bybit':
                    url = f"https://api.bybit.com/v5/market/instruments-info?category={self.category}&symbol={trading_pair}"
                    response = requests.get(url, timeout=10)
                    response.raise_for_status()
                    data = response.json()
                    if data['retCode'] == 0:
                        base_precision = safe_float(data['result']['list'][0]['lotSizeFilter']['basePrecision'])
                    else:
                        logger.error(f"Ошибка получения base_precision на Bybit для {trading_pair}: {data['retMsg']}")
                        base_precision = 0.001
                elif self.exchange == 'binance':
                    url = "https://api.binance.com/api/v3/exchangeInfo" if self.category == 'spot' else "https://fapi.binance.com/fapi/v1/exchangeInfo"
                    response = requests.get(url, timeout=10)
                    response.raise_for_status()
                    data = response.json()
                    for symbol in data['symbols']:
                        if symbol['symbol'] == trading_pair:
                            base_precision = safe_float(symbol['quantityPrecision'])
                            break
                    else:
                        logger.error(f"Торговая пара {trading_pair} не найдена на Binance")
                        base_precision = 0.001
                elif self.exchange == 'okx':
                    url = f"https://www.okx.com/api/v5/public/instruments?instType={'SPOT' if self.category == 'spot' else 'SWAP'}"
                    response = requests.get(url, timeout=10)
                    response.raise_for_status()
                    data = response.json()
                    if data['code'] == '0':
                        for instrument in data['data']:
                            if instrument['instId'] == trading_pair.replace('/', '-'):
                                base_precision = safe_float(instrument['lotSz'])
                                break
                        else:
                            logger.error(f"Торговая пара {trading_pair} не найдена на OKX")
                            base_precision = 0.001
                    else:
                        logger.error(f"Ошибка получения base_precision на OKX для {trading_pair}: {data['msg']}")
                        base_precision = 0.001
                else:
                    logger.error(f"Биржа {self.exchange} не поддерживается для получения base_precision")
                    base_precision = 0.001

                cache.set(cache_key, base_precision, timeout=3600)
                logger.debug(f"Base precision для {trading_pair} закэширован: {base_precision}")
            except requests.RequestException as e:
                logger.error(f"Ошибка запроса base_precision для {trading_pair} на {self.exchange}: {str(e)}")
                base_precision = 0.001
            except Exception as e:
                logger.error(f"Неожиданная ошибка при получении base_precision для {trading_pair} на {self.exchange}: {str(e)}", exc_info=True)
                base_precision = 0.001

        qty = math.floor(qty / base_precision) * base_precision
        return qty

    def update_position(self, price, qty):
        """
        Обновляет текущую позицию и среднюю цену.

        Args:
            price (float): Цена покупки.
            qty (float): Количество.
        """
        if self.position == 0:
            self.position = qty
            self.avg_price = price
        else:
            total_qty = self.position + qty
            self.avg_price = ((self.avg_price * self.position) + (price * qty)) / total_qty
            self.position = total_qty
        self.position_opened = True
        self.position_obj.position = self.position
        self.position_obj.avg_price = self.avg_price
        self.position_obj.position_opened = self.position_opened
        self.position_obj.save()
        logger.debug(f"Позиция обновлена: bot_id={self.bot.id}, position={self.position}, avg_price={self.avg_price}")

    def close_position(self, profit):
        """
        Закрывает позицию и сбрасывает параметры.

        Args:
            profit (float): Прибыль или убыток от сделки.
        """
        self.position = 0
        self.avg_price = 0
        self.sell_order_id = None
        self.buy_orders = []
        self.position_opened = False
        self.highest_price = 0
        self.position_obj.position = self.position
        self.position_obj.avg_price = self.avg_price
        self.position_obj.sell_order_id = self.sell_order_id
        self.position_obj.buy_orders = self.buy_orders
        self.position_obj.position_opened = self.position_opened
        self.position_obj.highest_price = self.highest_price
        self.position_obj.save()
        logger.info(f"Позиция закрыта: bot_id={self.bot.id}, profit={profit}")

    def adjust_grid(self, current_price):
        """
        Подстраивает сетку ордеров при движении цены, если включён grid_follow.

        Args:
            current_price (float): Текущая цена.
        """
        if not self.grid_follow:
            return
        try:
            buy_levels = self.calculate_buy_levels(current_price)
            active_orders = self.buy_orders[:]
            for order_id in active_orders:
                self.cancel_order(order_id)
                self.buy_orders.remove(order_id)
            self.position_obj.buy_orders = self.buy_orders
            self.position_obj.save()
            min_order_size = self.get_min_order_size()
            for i, buy_price in enumerate(buy_levels[:self.grid_orders - len(self.buy_orders)]):
                qty = self.calculate_quantity(i)
                if qty < min_order_size:
                    logger.warning(f"Объём {qty} меньше минимального {min_order_size} для бота {self.bot.id}, пропускаем ордер")
                    continue
                result = self.place_order('buy', buy_price, qty)
                order_id = result['orderId']
                self.buy_orders.append(order_id)
                self.position_obj.buy_orders = self.buy_orders
                self.position_obj.save()
                logger.info(f"Сетка скорректирована: bot_id={self.bot.id}, new buy price={buy_price}, qty={qty}")
        except Exception as e:
            logger.error(f"Ошибка при корректировке сетки для бота {self.bot.id}: {str(e)}")

    def cancel_order(self, order_id):
        """
        Отменяет ордер на бирже.

        Args:
            order_id (str): ID ордера.
        """
        trading_pair = self.bot.trading_pair.replace('/', '') if self.bot.trading_pair else ''
        if not trading_pair:
            logger.error(f"Торговая пара не указана для бота {self.bot.id}")
            return

        if self.exchange == 'bybit':
            url = "https://api.bybit.com/v5/order/cancel"
            timestamp = str(get_bybit_server_time())
            params = {"category": self.category, "symbol": trading_pair, "orderId": order_id}
            payload = json.dumps(params, separators=(',', ':'), sort_keys=True)
            sign_str = timestamp + self.api_key + str(self.recv_window) + payload
            signature = hmac.new(self.api_secret.encode('utf-8'), sign_str.encode('utf-8'), hashlib.sha256).hexdigest()
            headers = {
                "X-BAPI-API-KEY": self.api_key,
                "X-BAPI-TIMESTAMP": timestamp,
                "X-BAPI-RECV-WINDOW": str(self.recv_window),
                "X-BAPI-SIGN": signature,
                "Content-Type": "application/json"
            }
            try:
                response = requests.post(url, headers=headers, data=payload, timeout=10)
                response.raise_for_status()
                data = response.json()
                if data['retCode'] != 0:
                    logger.error("Ошибка отмены ордера на Bybit: bot_id=%s, order_id=%s, error=%s", self.bot.id, order_id, data['retMsg'])
                else:
                    logger.info("Ордер отменён на Bybit: bot_id=%s, order_id=%s", self.bot.id, order_id)
            except requests.RequestException as e:
                logger.error("Ошибка запроса при отмене ордера на Bybit: bot_id=%s, order_id=%s, error=%s", self.bot.id, order_id, str(e))
        elif self.exchange == 'binance':
            url = "https://api.binance.com/api/v3/order" if self.category == 'spot' else "https://fapi.binance.com/fapi/v1/order"
            timestamp = str(int(time.time() * 1000))
            params = {
                "symbol": trading_pair,
                "orderId": order_id,
                "timestamp": timestamp
            }
            query_string = urlencode(sorted(params.items()))
            signature = hmac.new(self.api_secret.encode('utf-8'), query_string.encode('utf-8'), hashlib.sha256).hexdigest()
            params["signature"] = signature
            headers = {"X-MBX-APIKEY": self.api_key}
            try:
                response = requests.delete(url, headers=headers, params=params, timeout=10)
                response.raise_for_status()
                logger.info("Ордер отменён на Binance: bot_id=%s, order_id=%s", self.bot.id, order_id)
            except requests.RequestException as e:
                logger.error("Ошибка при отмене ордера на Binance: bot_id=%s, order_id=%s, error=%s", self.bot.id, order_id, str(e))
        elif self.exchange == 'okx':
            url = "https://www.okx.com/api/v5/trade/cancel-order"
            timestamp = str(int(time.time()))
            method = "POST"
            request_path = "/api/v5/trade/cancel-order"
            body = json.dumps({"instId": trading_pair.replace('/', '-'), "ordId": order_id})
            sign_str = timestamp + method + request_path + body
            signature = hmac.new(self.api_secret.encode('utf-8'), sign_str.encode('utf-8'), hashlib.sha256).hexdigest()
            headers = {
                "OK-ACCESS-KEY": self.api_key,
                "OK-ACCESS-SIGN": signature,
                "OK-ACCESS-TIMESTAMP": timestamp,
                "OK-ACCESS-PASSPHRASE": "",
                "Content-Type": "application/json"
            }
            try:
                response = requests.post(url, headers=headers, data=body, timeout=10)
                response.raise_for_status()
                data = response.json()
                if data['code'] == '0':
                    logger.info("Ордер отменён на OKX: bot_id=%s, order_id=%s", self.bot.id, order_id)
                else:
                    logger.error("Ошибка отмены ордера на OKX: bot_id=%s, order_id=%s, error=%s", self.bot.id, order_id, data['msg'])
            except requests.RequestException as e:
                logger.error("Ошибка при отмене ордера на OKX: bot_id=%s, order_id=%s, error=%s", self.bot.id, order_id, str(e))
        else:
            logger.error(f"Exchange {self.exchange} not supported для бота {self.bot.id}")
            raise NotImplementedError(f"Exchange {self.exchange} not supported")

    def cancel_all_orders(self):
        """
        Отменяет все активные ордера бота.
        """
        logger.info("Отмена всех ордеров для бота %s", self.bot.id)
        for order_id in self.buy_orders[:]:
            try:
                self.cancel_order(order_id)
                self.buy_orders.remove(order_id)
            except Exception as e:
                logger.error("Ошибка при отмене ордера на покупку: bot_id=%s, order_id=%s, error=%s", self.bot.id, order_id, str(e))

        if self.sell_order_id:
            try:
                self.cancel_order(self.sell_order_id)
                self.sell_order_id = None
            except Exception as e:
                logger.error("Ошибка при отмене ордера на продажу: bot_id=%s, order_id=%s, error=%s", self.bot.id, self.sell_order_id, str(e))

        self.position_obj.buy_orders = self.buy_orders
        self.position_obj.sell_order_id = self.sell_order_id
        self.position_obj.save()
        logger.info("Все ордера отменены для бота %s", self.bot.id)

    def stop_bot(self):
        """
        Останавливает бота и закрывает все открытые ордера.
        """
        try:
            self.bot.status = 'stopped'
            self.bot.is_running = False
            self.bot.save()
            self.cancel_all_orders()
            self.close_position(profit=0)
        except Exception as e:
            logger.error(f"Ошибка при остановке бота {self.bot.id}: {str(e)}", exc_info=True)
            raise
        finally:
            logger.info(f"Бот {self.bot.id} остановлен")

# Функция для остановки бота
def stop_bot(bot_id):
    """
    Останавливает бота, отменяет все его ордера и изменяет статус на 'stopped'.

    Args:
        bot_id (int): ID бота.
    """
    try:
        bot = Bot.objects.get(id=bot_id)
        strategy = TradingStrategy(bot)
        strategy.cancel_all_orders()  # Отменяем все ордера
        bot.status = 'stopped'
        bot.is_running = False
        bot.save()
        # Сбрасываем состояние позиции
        position = BotPosition.objects.get(bot=bot)
        position.position = 0
        position.avg_price = 0
        position.sell_order_id = None
        position.buy_orders = []
        position.position_opened = False
        position.save()
        logger.info(f"Бот {bot_id} остановлен")
    except Bot.DoesNotExist:
        logger.error(f"Бот с id={bot_id} не найден")
    except Exception as e:
        logger.error(f"Ошибка при остановке бота {bot_id}: {str(e)}")

# Задача Celery с проверкой статуса бота
@shared_task
def run_trading_strategy(bot_id):
    """
    Выполняет торговую стратегию для бота, если он активен.

    Args:
        bot_id (int): ID бота.
    """
    try:
        bot = Bot.objects.get(id=bot_id)
        if bot.status != 'active' or not bot.is_running:
            logger.info(f"Бот {bot_id} не активен или не запущен, пропуск задачи")
            return
        strategy = TradingStrategy(bot)
        strategy.execute()  # Выполнение торговой стратегии
        logger.info(f"Задача для бота {bot_id} выполнена")
    except Bot.DoesNotExist:
        logger.error(f"Бот с id={bot_id} не найден")
    except Exception as e:
        logger.error(f"Ошибка при выполнении стратегии для бота {bot_id}: {str(e)}", exc_info=True)
        raise