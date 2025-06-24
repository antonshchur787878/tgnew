# bots/indicators.py
import pandas as pd
from ta.momentum import RSIIndicator, WilliamsRIndicator, ROCIndicator, StochasticOscillator
from ta.trend import CCIIndicator, ADXIndicator, MACD, SMAIndicator, EMAIndicator, IchimokuIndicator
from ta.volatility import AverageTrueRange, BollingerBands
from ta.volume import MFIIndicator, ChaikinMoneyFlowIndicator
from django.core.cache import cache
from django.conf import settings
import logging
import statistics

logger = logging.getLogger(__name__)

# Настраиваемый таймаут кэширования для индикаторов
INDICATOR_CACHE_TIMEOUT = getattr(settings, 'INDICATOR_CACHE_TIMEOUT', 300)

def prepare_dataframe(high=None, low=None, close=None, volume=None):
    """
    Создает DataFrame из предоставленных данных.

    Args:
        high (list, optional): Список максимальных цен.
        low (list, optional): Список минимальных цен.
        close (list, optional): Список цен закрытия.
        volume (list, optional): Список объемов.

    Returns:
        pd.DataFrame: DataFrame с указанными столбцами.

    Raises:
        ValueError: Если не предоставлены данные для создания DataFrame.
    """
    data = {}
    if high is not None:
        data['high'] = high
    if low is not None:
        data['low'] = low
    if close is not None:
        data['close'] = close
    if volume is not None:
        data['volume'] = volume
    if not data:
        logger.error("Не предоставлены данные для создания DataFrame")
        raise ValueError("Не предоставлены данные для создания DataFrame")
    return pd.DataFrame(data)

def calculate_rsi(prices_tuple, period=14):
    """
    Рассчитывает индекс относительной силы (RSI) на основе цен закрытия.

    Args:
        prices_tuple (tuple): Кортеж цен закрытия (для кэширования).
        period (int): Период для расчета RSI (по умолчанию 14).

    Returns:
        float: Значение RSI для последней свечи, или None, если данных недостаточно.
    """
    cache_key = f"rsi_{hash(prices_tuple)}_{period}"
    result = cache.get(cache_key)
    if result is not None:
        logger.debug(f"RSI извлечен из кэша: {result}")
        return result

    prices = list(prices_tuple)
    if not prices or len(prices) < period:
        logger.warning(f"Недостаточно данных для расчета RSI: требуется минимум {period} значений, получено {len(prices)}")
        return None
    try:
        df = prepare_dataframe(close=prices)
        rsi = RSIIndicator(df['close'], window=period).rsi()
        result = rsi.iloc[-1] if not rsi.empty else None
        cache.set(cache_key, result, timeout=INDICATOR_CACHE_TIMEOUT)
        logger.debug(f"RSI рассчитан: {result}")
        return result
    except Exception as e:
        logger.error(f"Ошибка при расчете RSI: {str(e)}")
        return None

def calculate_cci(high_tuple, low_tuple, close_tuple, period=20):
    """
    Рассчитывает индекс товарного канала (CCI).

    Args:
        high_tuple (tuple): Кортеж максимальных цен.
        low_tuple (tuple): Кортеж минимальных цен.
        close_tuple (tuple): Кортеж цен закрытия.
        period (int): Период для расчета CCI (по умолчанию 20).

    Returns:
        float: Значение CCI для последней свечи, или None, если данных недостаточно.
    """
    cache_key = f"cci_{hash(high_tuple)}_{hash(low_tuple)}_{hash(close_tuple)}_{period}"
    result = cache.get(cache_key)
    if result is not None:
        logger.debug(f"CCI извлечен из кэша: {result}")
        return result

    high, low, close = list(high_tuple), list(low_tuple), list(close_tuple)
    if not all([high, low, close]) or len(high) < period:
        logger.warning(f"Недостаточно данных для расчета CCI: требуется минимум {period} значений, получено {len(high)}")
        return None
    try:
        df = prepare_dataframe(high=high, low=low, close=close)
        cci = CCIIndicator(df['high'], df['low'], df['close'], window=period).cci()
        result = cci.iloc[-1] if not cci.empty else None
        cache.set(cache_key, result, timeout=INDICATOR_CACHE_TIMEOUT)
        logger.debug(f"CCI рассчитан: {result}")
        return result
    except Exception as e:
        logger.error(f"Ошибка при расчете CCI: {str(e)}")
        return None

def calculate_mfi(high_tuple, low_tuple, close_tuple, volume_tuple, period=14):
    """
    Рассчитывает индекс денежного потока (MFI).

    Args:
        high_tuple (tuple): Кортеж максимальных цен.
        low_tuple (tuple): Кортеж минимальных цен.
        close_tuple (tuple): Кортеж цен закрытия.
        volume_tuple (tuple): Кортеж объемов.
        period (int): Период для расчета MFI (по умолчанию 14).

    Returns:
        float: Значение MFI для последней свечи, или None, если данных недостаточно.
    """
    cache_key = f"mfi_{hash(high_tuple)}_{hash(low_tuple)}_{hash(close_tuple)}_{hash(volume_tuple)}_{period}"
    result = cache.get(cache_key)
    if result is not None:
        logger.debug(f"MFI извлечен из кэша: {result}")
        return result

    high, low, close, volume = list(high_tuple), list(low_tuple), list(close_tuple), list(volume_tuple)
    if not all([high, low, close, volume]) or len(high) < period:
        logger.warning(f"Недостаточно данных для расчета MFI: требуется минимум {period} значений, получено {len(high)}")
        return None
    try:
        df = prepare_dataframe(high=high, low=low, close=close, volume=volume)
        mfi = MFIIndicator(df['high'], df['low'], df['close'], df['volume'], window=period).money_flow_index()
        result = mfi.iloc[-1] if not mfi.empty else None
        cache.set(cache_key, result, timeout=INDICATOR_CACHE_TIMEOUT)
        logger.debug(f"MFI рассчитан: {result}")
        return result
    except Exception as e:
        logger.error(f"Ошибка при расчете MFI: {str(e)}")
        return None

def calculate_adx(high_tuple, low_tuple, close_tuple, period=14):
    """
    Рассчитывает средний индекс направленного движения (ADX).

    Args:
        high_tuple (tuple): Кортеж максимальных цен.
        low_tuple (tuple): Кортеж минимальных цен.
        close_tuple (tuple): Кортеж цен закрытия.
        period (int): Период для расчета ADX (по умолчанию 14).

    Returns:
        float: Значение ADX для последней свечи, или None, если данных недостаточно.
    """
    cache_key = f"adx_{hash(high_tuple)}_{hash(low_tuple)}_{hash(close_tuple)}_{period}"
    result = cache.get(cache_key)
    if result is not None:
        logger.debug(f"ADX извлечен из кэша: {result}")
        return result

    high, low, close = list(high_tuple), list(low_tuple), list(close_tuple)
    if not all([high, low, close]) or len(high) < period:
        logger.warning(f"Недостаточно данных для расчета ADX: требуется минимум {period} значений, получено {len(high)}")
        return None
    try:
        df = prepare_dataframe(high=high, low=low, close=close)
        adx = ADXIndicator(df['high'], df['low'], df['close'], window=period).adx()
        result = adx.iloc[-1] if not adx.empty else None
        cache.set(cache_key, result, timeout=INDICATOR_CACHE_TIMEOUT)
        logger.debug(f"ADX рассчитан: {result}")
        return result
    except Exception as e:
        logger.error(f"Ошибка при расчете ADX: {str(e)}")
        return None

def calculate_atr(high_tuple, low_tuple, close_tuple, period=14):
    """
    Рассчитывает средний истинный диапазон (ATR).

    Args:
        high_tuple (tuple): Кортеж максимальных цен.
        low_tuple (tuple): Кортеж минимальных цен.
        close_tuple (tuple): Кортеж цен закрытия.
        period (int): Период для расчета ATR (по умолчанию 14).

    Returns:
        float: Значение ATR для последней свечи, или None, если данных недостаточно.
    """
    cache_key = f"atr_{hash(high_tuple)}_{hash(low_tuple)}_{hash(close_tuple)}_{period}"
    result = cache.get(cache_key)
    if result is not None:
        logger.debug(f"ATR извлечен из кэша: {result}")
        return result

    high, low, close = list(high_tuple), list(low_tuple), list(close_tuple)
    if not all([high, low, close]) or len(high) < period:
        logger.warning(f"Недостаточно данных для расчета ATR: требуется минимум {period} значений, получено {len(high)}")
        return None
    try:
        df = prepare_dataframe(high=high, low=low, close=close)
        atr = AverageTrueRange(df['high'], df['low'], df['close'], window=period).average_true_range()
        result = atr.iloc[-1] if not atr.empty else None
        cache.set(cache_key, result, timeout=INDICATOR_CACHE_TIMEOUT)
        logger.debug(f"ATR рассчитан: {result}")
        return result
    except Exception as e:
        logger.error(f"Ошибка при расчете ATR: {str(e)}")
        return None

def calculate_williams_r(high_tuple, low_tuple, close_tuple, period=14):
    """
    Рассчитывает индикатор Williams %R.

    Args:
        high_tuple (tuple): Кортеж максимальных цен.
        low_tuple (tuple): Кортеж минимальных цен.
        close_tuple (tuple): Кортеж цен закрытия.
        period (int): Период для расчета Williams %R (по умолчанию 14).

    Returns:
        float: Значение Williams %R для последней свечи, или None, если данных недостаточно.
    """
    cache_key = f"williams_r_{hash(high_tuple)}_{hash(low_tuple)}_{hash(close_tuple)}_{period}"
    result = cache.get(cache_key)
    if result is not None:
        logger.debug(f"Williams %R извлечен из кэша: {result}")
        return result

    high, low, close = list(high_tuple), list(low_tuple), list(close_tuple)
    if not all([high, low, close]) or len(high) < period:
        logger.warning(f"Недостаточно данных для расчета Williams %R: требуется минимум {period} значений, получено {len(high)}")
        return None
    try:
        df = prepare_dataframe(high=high, low=low, close=close)
        williams_r = WilliamsRIndicator(df['high'], df['low'], df['close'], window=period).williams_r()
        result = williams_r.iloc[-1] if not williams_r.empty else None
        cache.set(cache_key, result, timeout=INDICATOR_CACHE_TIMEOUT)
        logger.debug(f"Williams %R рассчитан: {result}")
        return result
    except Exception as e:
        logger.error(f"Ошибка при расчете Williams %R: {str(e)}")
        return None

def calculate_roc(prices_tuple, period=12):
    """
    Рассчитывает скорость изменения (ROC).

    Args:
        prices_tuple (tuple): Кортеж цен закрытия.
        period (int): Период для расчета ROC (по умолчанию 12).

    Returns:
        float: Значение ROC для последней свечи, или None, если данных недостаточно.
    """
    cache_key = f"roc_{hash(prices_tuple)}_{period}"
    result = cache.get(cache_key)
    if result is not None:
        logger.debug(f"ROC извлечен из кэша: {result}")
        return result

    prices = list(prices_tuple)
    if not prices or len(prices) < period:
        logger.warning(f"Недостаточно данных для расчета ROC: требуется минимум {period} значений, получено {len(prices)}")
        return None
    try:
        df = prepare_dataframe(close=prices)
        roc = ROCIndicator(df['close'], window=period).roc()
        result = roc.iloc[-1] if not roc.empty else None
        cache.set(cache_key, result, timeout=INDICATOR_CACHE_TIMEOUT)
        logger.debug(f"ROC рассчитан: {result}")
        return result
    except Exception as e:
        logger.error(f"Ошибка при расчете ROC: {str(e)}")
        return None

def calculate_macd(close_tuple, fast_period=12, slow_period=26, signal_period=9):
    """
    Рассчитывает MACD (Moving Average Convergence Divergence).

    Args:
        close_tuple (tuple): Кортеж цен закрытия.
        fast_period (int): Период быстрой EMA (по умолчанию 12).
        slow_period (int): Период медленной EMA (по умолчанию 26).
        signal_period (int): Период сигнальной линии (по умолчанию 9).

    Returns:
        tuple: (MACD, Signal, Histogram) для последней свечи, или (None, None, None), если данных недостаточно.
    """
    cache_key = f"macd_{hash(close_tuple)}_{fast_period}_{slow_period}_{signal_period}"
    result = cache.get(cache_key)
    if result is not None:
        logger.debug(f"MACD извлечен из кэша: {result}")
        return result

    close = list(close_tuple)
    if not close or len(close) < slow_period:
        logger.warning(f"Недостаточно данных для расчета MACD: требуется минимум {slow_period} значений, получено {len(close)}")
        return None, None, None
    try:
        df = prepare_dataframe(close=close)
        macd = MACD(df['close'], window_fast=fast_period, window_slow=slow_period, window_sign=signal_period)
        result = (
            macd.macd().iloc[-1] if not macd.macd().empty else None,
            macd.macd_signal().iloc[-1] if not macd.macd_signal().empty else None,
            macd.macd_diff().iloc[-1] if not macd.macd_diff().empty else None
        )
        cache.set(cache_key, result, timeout=INDICATOR_CACHE_TIMEOUT)
        logger.debug(f"MACD рассчитан: {result}")
        return result
    except Exception as e:
        logger.error(f"Ошибка при расчете MACD: {str(e)}")
        return None, None, None

def calculate_sma(prices_tuple, period=20):
    """
    Рассчитывает простую скользящую среднюю (SMA).

    Args:
        prices_tuple (tuple): Кортеж цен закрытия.
        period (int): Период для расчета SMA (по умолчанию 20).

    Returns:
        float: Значение SMA для последней свечи, или None, если данных недостаточно.
    """
    cache_key = f"sma_{hash(prices_tuple)}_{period}"
    result = cache.get(cache_key)
    if result is not None:
        logger.debug(f"SMA извлечен из кэша: {result}")
        return result

    prices = list(prices_tuple)
    if not prices or len(prices) < period:
        logger.warning(f"Недостаточно данных для расчета SMA: требуется минимум {period} значений, получено {len(prices)}")
        return None
    try:
        df = prepare_dataframe(close=prices)
        sma = SMAIndicator(df['close'], window=period).sma_indicator()
        result = sma.iloc[-1] if not sma.empty else None
        cache.set(cache_key, result, timeout=INDICATOR_CACHE_TIMEOUT)
        logger.debug(f"SMA рассчитан: {result}")
        return result
    except Exception as e:
        logger.error(f"Ошибка при расчете SMA: {str(e)}")
        return None

def calculate_ema(prices_tuple, period=20):
    """
    Рассчитывает экспоненциальную скользящую среднюю (EMA).

    Args:
        prices_tuple (tuple): Кортеж цен закрытия.
        period (int): Период для расчета EMA (по умолчанию 20).

    Returns:
        float: Значение EMA для последней свечи, или None, если данных недостаточно.
    """
    cache_key = f"ema_{hash(prices_tuple)}_{period}"
    result = cache.get(cache_key)
    if result is not None:
        logger.debug(f"EMA извлечен из кэша: {result}")
        return result

    prices = list(prices_tuple)
    if not prices or len(prices) < period:
        logger.warning(f"Недостаточно данных для расчета EMA: требуется минимум {period} значений, получено {len(prices)}")
        return None
    try:
        df = prepare_dataframe(close=prices)
        ema = EMAIndicator(df['close'], window=period).ema_indicator()
        result = ema.iloc[-1] if not ema.empty else None
        cache.set(cache_key, result, timeout=INDICATOR_CACHE_TIMEOUT)
        logger.debug(f"EMA рассчитан: {result}")
        return result
    except Exception as e:
        logger.error(f"Ошибка при расчете EMA: {str(e)}")
        return None

def calculate_bollinger_bands(prices_tuple, period=20, dev=2):
    """
    Рассчитывает полосы Боллинджера.

    Args:
        prices_tuple (tuple): Кортеж цен закрытия.
        period (int): Период для расчета (по умолчанию 20).
        dev (float): Количество стандартных отклонений (по умолчанию 2).

    Returns:
        tuple: (Upper Band, Lower Band, Middle Band) для последней свечи, или (None, None, None), если данных недостаточно.
    """
    cache_key = f"bollinger_bands_{hash(prices_tuple)}_{period}_{dev}"
    result = cache.get(cache_key)
    if result is not None:
        logger.debug(f"Bollinger Bands извлечены из кэша: {result}")
        return result

    prices = list(prices_tuple)
    if not prices or len(prices) < period:
        logger.warning(f"Недостаточно данных для расчета Bollinger Bands: требуется минимум {period} значений, получено {len(prices)}")
        return None, None, None
    try:
        df = prepare_dataframe(close=prices)
        bb = BollingerBands(df['close'], window=period, window_dev=dev)
        result = (
            bb.bollinger_hband().iloc[-1] if not bb.bollinger_hband().empty else None,
            bb.bollinger_lband().iloc[-1] if not bb.bollinger_lband().empty else None,
            bb.bollinger_mavg().iloc[-1] if not bb.bollinger_mavg().empty else None
        )
        cache.set(cache_key, result, timeout=INDICATOR_CACHE_TIMEOUT)
        logger.debug(f"Bollinger Bands рассчитаны: {result}")
        return result
    except Exception as e:
        logger.error(f"Ошибка при расчете Bollinger Bands: {str(e)}")
        return None, None, None

def calculate_stochastic(high_tuple, low_tuple, close_tuple, k_period=14, d_period=3):
    """
    Рассчитывает стохастический осциллятор.

    Args:
        high_tuple (tuple): Кортеж максимальных цен.
        low_tuple (tuple): Кортеж минимальных цен.
        close_tuple (tuple): Кортеж цен закрытия.
        k_period (int): Период для %K (по умолчанию 14).
        d_period (int): Период для %D (по умолчанию 3).

    Returns:
        tuple: (%K, %D) для последней свечи, или (None, None), если данных недостаточно.
    """
    cache_key = f"stochastic_{hash(high_tuple)}_{hash(low_tuple)}_{hash(close_tuple)}_{k_period}_{d_period}"
    result = cache.get(cache_key)
    if result is not None:
        logger.debug(f"Stochastic извлечен из кэша: {result}")
        return result

    high, low, close = list(high_tuple), list(low_tuple), list(close_tuple)
    if not all([high, low, close]) or len(high) < k_period:
        logger.warning(f"Недостаточно данных для расчета Stochastic: требуется минимум {k_period} значений, получено {len(high)}")
        return None, None
    try:
        df = prepare_dataframe(high=high, low=low, close=close)
        stoch = StochasticOscillator(df['high'], df['low'], df['close'], window=k_period, smooth_window=d_period)
        result = (
            stoch.stoch().iloc[-1] if not stoch.stoch().empty else None,
            stoch.stoch_signal().iloc[-1] if not stoch.stoch_signal().empty else None
        )
        cache.set(cache_key, result, timeout=INDICATOR_CACHE_TIMEOUT)
        logger.debug(f"Stochastic рассчитан: {result}")
        return result
    except Exception as e:
        logger.error(f"Ошибка при расчете Stochastic: {str(e)}")
        return None, None

def calculate_chaikin_oscillator(high_tuple, low_tuple, close_tuple, volume_tuple, period=10):
    """
    Рассчитывает осциллятор Чайкина.

    Args:
        high_tuple (tuple): Кортеж максимальных цен.
        low_tuple (tuple): Кортеж минимальных цен.
        close_tuple (tuple): Кортеж цен закрытия.
        volume_tuple (tuple): Кортеж объемов.
        period (int): Период для расчета (по умолчанию 10).

    Returns:
        float: Значение Chaikin Money Flow для последней свечи, или None, если данных недостаточно.
    """
    cache_key = f"chaikin_oscillator_{hash(high_tuple)}_{hash(low_tuple)}_{hash(close_tuple)}_{hash(volume_tuple)}_{period}"
    result = cache.get(cache_key)
    if result is not None:
        logger.debug(f"Chaikin Oscillator извлечен из кэша: {result}")
        return result

    high, low, close, volume = list(high_tuple), list(low_tuple), list(close_tuple), list(volume_tuple)
    if not all([high, low, close, volume]) or len(high) < period:
        logger.warning(f"Недостаточно данных для расчета Chaikin: требуется минимум {period} значений, получено {len(high)}")
        return None
    try:
        df = prepare_dataframe(high=high, low=low, close=close, volume=volume)
        cmf = ChaikinMoneyFlowIndicator(df['high'], df['low'], df['close'], df['volume'], window=period)
        result = cmf.chaikin_money_flow().iloc[-1] if not cmf.chaikin_money_flow().empty else None
        cache.set(cache_key, result, timeout=INDICATOR_CACHE_TIMEOUT)
        logger.debug(f"Chaikin Oscillator рассчитан: {result}")
        return result
    except Exception as e:
        logger.error(f"Ошибка при расчете Chaikin Oscillator: {str(e)}")
        return None

def calculate_ichimoku(high_tuple, low_tuple, close_tuple, tenkan_period=9, kijun_period=26, senkou_period=52):
    """
    Рассчитывает индикатор Облака Ишимоку.

    Args:
        high_tuple (tuple): Кортеж максимальных цен.
        low_tuple (tuple): Кортеж минимальных цен.
        close_tuple (tuple): Кортеж цен закрытия.
        tenkan_period (int): Период Tenkan-sen (по умолчанию 9).
        kijun_period (int): Период Kijun-sen (по умолчанию 26).
        senkou_period (int): Период Senkou Span (по умолчанию 52).

    Returns:
        tuple: (Senkou Span A, Senkou Span B, Kijun-sen, Tenkan-sen) для последней свечи, или (None, None, None, None), если данных недостаточно.
    """
    cache_key = f"ichimoku_{hash(high_tuple)}_{hash(low_tuple)}_{hash(close_tuple)}_{tenkan_period}_{kijun_period}_{senkou_period}"
    result = cache.get(cache_key)
    if result is not None:
        logger.debug(f"Ichimoku извлечен из кэша: {result}")
        return result

    high, low, close = list(high_tuple), list(low_tuple), list(close_tuple)
    if not all([high, low, close]) or len(high) < max(tenkan_period, kijun_period, senkou_period):
        logger.warning(f"Недостаточно данных для расчета Ichimoku: требуется минимум {max(tenkan_period, kijun_period, senkou_period)} значений, получено {len(high)}")
        return None, None, None, None
    try:
        df = prepare_dataframe(high=high, low=low, close=close)
        ichimoku = IchimokuIndicator(df['high'], df['low'], window1=tenkan_period, window2=kijun_period, window3=senkou_period)
        result = (
            ichimoku.ichimoku_a().iloc[-1] if not ichimoku.ichimoku_a().empty else None,
            ichimoku.ichimoku_b().iloc[-1] if not ichimoku.ichimoku_b().empty else None,
            ichimoku.ichimoku_base_line().iloc[-1] if not ichimoku.ichimoku_base_line().empty else None,
            ichimoku.ichimoku_conversion_line().iloc[-1] if not ichimoku.ichimoku_conversion_line().empty else None
        )
        cache.set(cache_key, result, timeout=INDICATOR_CACHE_TIMEOUT)
        logger.debug(f"Ichimoku рассчитан: {result}")
        return result
    except Exception as e:
        logger.error(f"Ошибка при расчете Ichimoku: {str(e)}")
        return None, None, None, None

def calculate_volume_spike(volume_tuple, lookback=10):
    """
    Рассчитывает, есть ли резкий рост объема (Volume Spike).

    Args:
        volume_tuple (tuple): Кортеж объемов.
        lookback (int): Период для расчета среднего объема (по умолчанию 10).

    Returns:
        tuple: (current_volume, avg_volume) для последней свечи, или (None, None), если данных недостаточно.
    """
    cache_key = f"volume_spike_{hash(volume_tuple)}_{lookback}"
    result = cache.get(cache_key)
    if result is not None:
        logger.debug(f"Volume Spike извлечен из кэша: {result}")
        return result

    volumes = list(volume_tuple)
    if not volumes or len(volumes) < lookback + 1:
        logger.warning(f"Недостаточно данных для расчета Volume Spike: требуется минимум {lookback + 1} значений, получено {len(volumes)}")
        return None, None
    try:
        avg_volume = statistics.mean(volumes[-lookback-1:-1])
        current_volume = volumes[-1]
        result = (current_volume, avg_volume)
        cache.set(cache_key, result, timeout=INDICATOR_CACHE_TIMEOUT)
        logger.debug(f"Volume Spike рассчитан: current={current_volume}, avg={avg_volume}")
        return result
    except Exception as e:
        logger.error(f"Ошибка при расчете Volume Spike: {str(e)}")
        return None, None

def calculate_ma_crossover(close_tuple, short_period=10, long_period=20, ma_type='sma'):
    """
    Рассчитывает пересечение двух скользящих средних (MA Crossover).

    Args:
        close_tuple (tuple): Кортеж цен закрытия.
        short_period (int): Период короткой MA (по умолчанию 10).
        long_period (int): Период длинной MA (по умолчанию 20).
        ma_type (str): Тип скользящей средней ('sma' или 'ema', по умолчанию 'sma').

    Returns:
        tuple: (short_ma, long_ma, prev_short_ma, prev_long_ma) для последней свечи, или (None, None, None, None), если данных недостаточно.
    """
    cache_key = f"ma_crossover_{hash(close_tuple)}_{short_period}_{long_period}_{ma_type}"
    result = cache.get(cache_key)
    if result is not None:
        logger.debug(f"MA Crossover извлечен из кэша: {result}")
        return result

    closes = list(close_tuple)
    if not closes or len(closes) < long_period + 1:
        logger.warning(f"Недостаточно данных для расчета MA Crossover: требуется минимум {long_period + 1} значений, получено {len(closes)}")
        return None, None, None, None
    try:
        df = prepare_dataframe(close=closes)
        
        # Выбираем тип скользящей средней
        if ma_type == 'sma':
            ShortMAIndicator = SMAIndicator
            LongMAIndicator = SMAIndicator
        elif ma_type == 'ema':
            ShortMAIndicator = EMAIndicator
            LongMAIndicator = EMAIndicator
        else:
            logger.error(f"Неподдерживаемый тип скользящей средней: {ma_type}")
            return None, None, None, None

        # Рассчитываем короткую и длинную скользящие средние
        short_ma_series = ShortMAIndicator(df['close'], window=short_period).sma_indicator() if ma_type == 'sma' else ShortMAIndicator(df['close'], window=short_period).ema_indicator()
        long_ma_series = LongMAIndicator(df['close'], window=long_period).sma_indicator() if ma_type == 'sma' else LongMAIndicator(df['close'], window=long_period).ema_indicator()

        short_ma = short_ma_series.iloc[-1] if not short_ma_series.empty else None
        long_ma = long_ma_series.iloc[-1] if not long_ma_series.empty else None
        prev_short_ma = short_ma_series.iloc[-2] if len(short_ma_series) >= 2 else None
        prev_long_ma = long_ma_series.iloc[-2] if len(long_ma_series) >= 2 else None

        result = (short_ma, long_ma, prev_short_ma, prev_long_ma)
        cache.set(cache_key, result, timeout=INDICATOR_CACHE_TIMEOUT)
        logger.debug(f"MA Crossover рассчитан (тип {ma_type}): short_ma={short_ma}, long_ma={long_ma}, prev_short_ma={prev_short_ma}, prev_long_ma={prev_long_ma}")
        return result
    except Exception as e:
        logger.error(f"Ошибка при расчете MA Crossover: {str(e)}")
        return None, None, None, None

def calculate_pivot_points(high_tuple, low_tuple, close_tuple, period='D'):
    """
    Рассчитывает уровни Pivot Points (точки разворота) на основе указанного периода.

    Args:
        high_tuple (tuple): Кортеж максимальных цен.
        low_tuple (tuple): Кортеж минимальных цен.
        close_tuple (tuple): Кортеж цен закрытия.
        period (str): Период для расчета ('1h', '4h', 'D', 'W', 'M', по умолчанию 'D').

    Returns:
        tuple: (pivot, r1, s1) для последней свечи, или (None, None, None), если данных недостаточно.
    """
    # Определяем количество свечей в зависимости от периода (предполагаем, что свечи 1-часовые)
    period_map = {
        '1h': 1,
        '4h': 4,
        'D': 24,  # 24 часа в дне
        'W': 24 * 7,  # 7 дней в неделе
        'M': 24 * 30,  # 30 дней в месяце
    }
    lookback = period_map.get(period, 24)  # По умолчанию дневной период

    cache_key = f"pivot_points_{hash(high_tuple)}_{hash(low_tuple)}_{hash(close_tuple)}_{period}"
    result = cache.get(cache_key)
    if result is not None:
        logger.debug(f"Pivot Points извлечены из кэша: {result}")
        return result

    highs, lows, closes = list(high_tuple), list(low_tuple), list(close_tuple)
    if not all([highs, lows, closes]) or len(highs) < lookback:
        logger.warning(f"Недостаточно данных для расчета Pivot Points: требуется минимум {lookback} значений, получено {len(highs)}")
        return None, None, None
    try:
        # Берем данные за указанный период
        period_highs = highs[-lookback:]
        period_lows = lows[-lookback:]
        period_closes = closes[-lookback:]

        high = max(period_highs)
        low = min(period_lows)
        close = period_closes[-1]

        pivot = (high + low + close) / 3
        r1 = 2 * pivot - low  # Первое сопротивление
        s1 = 2 * pivot - high  # Первая поддержка

        result = (pivot, r1, s1)
        cache.set(cache_key, result, timeout=INDICATOR_CACHE_TIMEOUT)
        logger.debug(f"Pivot Points рассчитаны для периода {period}: pivot={pivot}, r1={r1}, s1={s1}")
        return result
    except Exception as e:
        logger.error(f"Ошибка при расчете Pivot Points: {str(e)}")
        return None, None, None