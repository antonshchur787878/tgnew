# bots/utils.py
import logging
import requests
import hmac
import hashlib
import time
import json
from urllib.parse import urlencode
from ratelimit import limits, sleep_and_retry
import math

logger = logging.getLogger(__name__)

def get_bybit_server_time():
    """
    Получает текущее время с сервера Bybit для синхронизации запросов.

    Returns:
        int: Время в миллисекундах.

    Raises:
        Exception: Если не удалось получить время сервера и запасной вариант не сработал.
    """
    url = "https://api.bybit.com/v5/market/time"
    try:
        response = requests.get(url, timeout=10)
        response.raise_for_status()
        data = response.json()
        if data['retCode'] == 0:
            logger.debug("Успешно получено время сервера Bybit: %s", data['result']['timeNano'])
            return int(data['result']['timeNano']) // 1_000_000  # Переводим из наносекунд в миллисекунды
        else:
            logger.error("Не удалось получить время сервера Bybit: %s", data['retMsg'])
            raise Exception(f"Failed to get Bybit server time: {data['retMsg']}")
    except requests.RequestException as e:
        logger.error("Ошибка запроса времени сервера Bybit: %s", str(e))
        logger.warning("Используем локальное время как запасной вариант")
        return int(time.time() * 1000)  # Локальное время в миллисекундах

def safe_float(value, default=0.0):
    """
    Безопасно преобразует строку в float.

    Args:
        value (str): Значение для преобразования.
        default (float): Значение по умолчанию, если преобразование не удалось.

    Returns:
        float: Преобразованное значение или значение по умолчанию.
    """
    try:
        return float(value) if value else default
    except (ValueError, TypeError):
        return default

class ExchangeAPI:
    """
    Класс для работы с API различных бирж.
    """
    @staticmethod
    @sleep_and_retry
    @limits(calls=10, period=1)  # 10 запросов в секунду
    def get_trading_pairs(exchange, category='spot'):
        """
        Получает список доступных торговых пар на указанной бирже.

        Args:
            exchange (str): Название биржи ('bybit', 'binance', 'okx').
            category (str): Категория ('spot' или 'futures').

        Returns:
            list: Список торговых пар (например: ['BTCUSDT', 'ETHUSDT']).

        Raises:
            ValueError: Если биржа или категория не поддерживается.
            ConnectionError: Если запрос к API завершился ошибкой.
        """
        logger.info(f"Получение торговых пар для {exchange}, category={category}")
        if exchange == 'bybit':
            url = f"https://api.bybit.com/v5/market/instruments-info?category={'spot' if category == 'spot' else 'linear'}"
            try:
                response = requests.get(url, timeout=10)
                response.raise_for_status()
                data = response.json()
                if data['retCode'] == 0:
                    pairs = [item['symbol'] for item in data.get('result', {}).get('list', []) if 'symbol' in item]
                    logger.debug(f"Получено {len(pairs)} торговых пар для Bybit ({category})")
                    return pairs
                else:
                    logger.error(f"Ошибка получения торговых пар для Bybit: {data['retMsg']}")
                    raise ValueError(f"Failed to get trading pairs: {data['retMsg']}")
            except requests.RequestException as e:
                logger.error(f"Ошибка запроса торговых пар для Bybit: {str(e)}")
                raise ConnectionError(f"Ошибка подключения к Bybit: {str(e)}")
        elif exchange == 'binance':
            url = "https://api.binance.com/api/v3/exchangeInfo" if category == 'spot' else "https://fapi.binance.com/fapi/v1/exchangeInfo"
            try:
                response = requests.get(url, timeout=10)
                response.raise_for_status()
                data = response.json()
                pairs = [item['symbol'] for item in data.get('symbols', []) if 'symbol' in item]
                logger.debug(f"Получено {len(pairs)} торговых пар для Binance ({category})")
                return pairs
            except requests.RequestException as e:
                logger.error(f"Ошибка запроса торговых пар для Binance: {str(e)}")
                raise ConnectionError(f"Ошибка подключения к Binance: {str(e)}")
        elif exchange == 'okx':
            inst_type = 'SPOT' if category == 'spot' else 'FUTURES'
            url = f"https://www.okx.com/api/v5/public/instruments?instType={inst_type}"
            try:
                response = requests.get(url, timeout=10)
                response.raise_for_status()
                data = response.json()
                if data['code'] == '0':
                    pairs = [item['instId'].replace('-', '') for item in data.get('data', []) if 'instId' in item]
                    logger.debug(f"Получено {len(pairs)} торговых пар для OKX ({category})")
                    return pairs
                else:
                    logger.error(f"Ошибка получения торговых пар для OKX: {data['msg']}")
                    raise ValueError(f"Failed to get trading pairs: {data['msg']}")
            except requests.RequestException as e:
                logger.error(f"Ошибка запроса торговых пар для OKX: {str(e)}")
                raise ConnectionError(f"Ошибка подключения к OKX: {str(e)}")
        else:
            logger.error(f"Биржа {exchange} не поддерживается")
            raise ValueError(f"Биржа {exchange} не поддерживается")

    @staticmethod
    @sleep_and_retry
    @limits(calls=10, period=1)
    def validate_api_key(exchange, api_key, api_secret):
        """
        Проверяет валидность API-ключа, делая тестовый запрос.

        Args:
            exchange (str): Название биржи ('bybit', 'binance', 'okx').
            api_key (str): API-ключ.
            api_secret (str): Секретный ключ.

        Raises:
            ValueError: Если ключ недействителен или биржа не поддерживается.
        """
        logger.info(f"Валидация API-ключа для {exchange}")
        try:
            if exchange == 'bybit':
                url = "https://api.bybit.com/v5/user/query-api"
                timestamp = str(get_bybit_server_time())
                recv_window = "5000"
                sign_str = timestamp + api_key + recv_window
                signature = hmac.new(api_secret.encode('utf-8'), sign_str.encode('utf-8'), hashlib.sha256).hexdigest()
                headers = {
                    "X-BAPI-API-KEY": api_key,
                    "X-BAPI-TIMESTAMP": timestamp,
                    "X-BAPI-RECV-WINDOW": recv_window,
                    "X-BAPI-SIGN": signature
                }
                response = requests.get(url, headers=headers, timeout=10)
                response.raise_for_status()
                data = response.json()
                if data['retCode'] != 0:
                    logger.error(f"Ошибка валидации API-ключа для Bybit: {data['retMsg']}")
                    raise ValueError(f"Invalid API key: {data['retMsg']}")
            elif exchange == 'binance':
                url = "https://api.binance.com/api/v3/account"
                timestamp = str(int(time.time() * 1000))
                params = {"timestamp": timestamp}
                query_string = urlencode(sorted(params.items()))
                signature = hmac.new(api_secret.encode('utf-8'), query_string.encode('utf-8'), hashlib.sha256).hexdigest()
                params["signature"] = signature
                headers = {"X-MBX-APIKEY": api_key}
                response = requests.get(url, headers=headers, params=params, timeout=10)
                response.raise_for_status()
                data = response.json()
                if 'code' in data and data['code'] != 200:
                    logger.error(f"Ошибка валидации API-ключа для Binance: {data['msg']}")
                    raise ValueError(f"Invalid API key: {data['msg']}")
            elif exchange == 'okx':
                url = "https://www.okx.com/api/v5/account/balance"
                timestamp = str(int(time.time()))
                method = "GET"
                request_path = "/api/v5/account/balance"
                body = ""
                sign_str = timestamp + method + request_path + body
                signature = hmac.new(api_secret.encode('utf-8'), sign_str.encode('utf-8'), hashlib.sha256).hexdigest()
                headers = {
                    "OK-ACCESS-KEY": api_key,
                    "OK-ACCESS-SIGN": signature,
                    "OK-ACCESS-TIMESTAMP": timestamp,
                    "OK-ACCESS-PASSPHRASE": ""
                }
                response = requests.get(url, headers=headers, timeout=10)
                response.raise_for_status()
                data = response.json()
                if data['code'] != '0':
                    logger.error(f"Ошибка валидации API-ключа для OKX: {data['msg']}")
                    raise ValueError(f"Invalid API key: {data['msg']}")
            else:
                raise NotImplementedError(f"Exchange {exchange} not supported")
            logger.info(f"API-ключ для {exchange} успешно валидирован")
        except requests.RequestException as e:
            logger.error(f"Ошибка запроса при валидации API-ключа для {exchange}: {str(e)}")
            raise ValueError(f"Ошибка валидации API-ключа: {str(e)}")

    @staticmethod
    @sleep_and_retry
    @limits(calls=10, period=1)
    def check_api_key_permissions(exchange, api_key, api_secret):
        """
        Проверяет права API-ключа, чтобы убедиться, что он имеет только права на торговлю.

        Args:
            exchange (str): Название биржи ('bybit', 'binance', 'okx').
            api_key (str): API-ключ.
            api_secret (str): Секретный ключ.

        Raises:
            ValueError: Если ключ имеет недопустимые права или невалиден.
        """
        logger.info(f"Проверка прав API-ключа для {exchange}")
        if exchange == 'bybit':
            url = "https://api.bybit.com/v5/user/query-api"
            timestamp = str(get_bybit_server_time())
            recv_window = "5000"
            sign_str = timestamp + api_key + recv_window
            signature = hmac.new(api_secret.encode('utf-8'), sign_str.encode('utf-8'), hashlib.sha256).hexdigest()
            headers = {
                "X-BAPI-API-KEY": api_key,
                "X-BAPI-TIMESTAMP": timestamp,
                "X-BAPI-RECV-WINDOW": recv_window,
                "X-BAPI-SIGN": signature
            }
            try:
                response = requests.get(url, headers=headers, timeout=10)
                response.raise_for_status()
                data = response.json()
                if data['retCode'] == 0:
                    permissions = data['result'].get('permissions', {})
                    if 'Withdraw' in permissions.get('Spot', []) or 'Withdraw' in permissions.get('Contract', []):
                        logger.error("API-ключ имеет права на вывод средств")
                        raise ValueError("API-ключ не должен иметь права на вывод средств")
                    if 'Trade' not in permissions.get('Spot', []) and 'Trade' not in permissions.get('Contract', []):
                        logger.error("API-ключ не имеет прав на торговлю")
                        raise ValueError("API-ключ должен иметь права на торговлю")
                    logger.info(f"Права API-ключа для {exchange} успешно проверены")
                else:
                    logger.error(f"Ошибка проверки прав API-ключа для Bybit: {data['retMsg']}")
                    raise ValueError(f"Ошибка проверки прав: {data['retMsg']}")
            except requests.RequestException as e:
                logger.error(f"Ошибка запроса при проверке прав API-ключа: {str(e)}")
                raise ValueError(f"Ошибка проверки прав API-ключа: {str(e)}")
        else:
            logger.warning(f"Проверка прав API-ключа для {exchange} не реализована")
            return

    @staticmethod
    @sleep_and_retry
    @limits(calls=10, period=1)
    def get_balance(exchange, api_key, api_secret, category='spot'):
        """
        Получает баланс пользователя на бирже, включая маржу для фьючерсов.

        Args:
            exchange (str): Название биржи ('bybit', 'binance', 'okx').
            api_key (str): API-ключ.
            api_secret (str): Секретный ключ.
            category (str): Категория ('spot' или 'futures').

        Returns:
            dict: Словарь с балансами активов, общим доступным балансом и данными о марже (для фьючерсов).

        Raises:
            ValueError: Если запрос завершился ошибкой или биржа не поддерживается.
        """
        logger.info(f"Получение баланса для {exchange}, category={category}")
        if exchange == 'bybit':
            url = "https://api.bybit.com/v5/account/wallet-balance"
            timestamp = str(get_bybit_server_time())
            recv_window = "5000"
            params = {"accountType": "UNIFIED" if category == 'futures' else "SPOT"}
            query_string = urlencode(sorted(params.items()))
            sign_str = timestamp + api_key + recv_window + query_string
            signature = hmac.new(api_secret.encode('utf-8'), sign_str.encode('utf-8'), hashlib.sha256).hexdigest()
            headers = {
                "X-BAPI-API-KEY": api_key,
                "X-BAPI-TIMESTAMP": timestamp,
                "X-BAPI-RECV-WINDOW": recv_window,
                "X-BAPI-SIGN": signature,
            }
            try:
                response = requests.get(url, headers=headers, params=params, timeout=10)
                response.raise_for_status()
                data = response.json()
                if data['retCode'] == 0:
                    account_info = data['result']['list'][0]
                    total_available = safe_float(account_info.get('totalAvailableBalance', "0"))
                    margin_data = {
                        'margin_balance': safe_float(account_info.get('totalMarginBalance', "0")),
                        'unrealized_pnl': safe_float(account_info.get('totalPerpUPL', "0")),
                        'margin_ratio': safe_float(account_info.get('marginRatio', "0"))
                    } if category == 'futures' else {}
                    balances = {
                        coin['coin']: {
                            "total": safe_float(coin['equity']),
                            "available": safe_float(coin['availableToWithdraw'])
                        } for coin in account_info.get('coin', []) if safe_float(coin['equity']) > 0
                    }
                    logger.debug(f"Баланс для Bybit ({category}): total_available={total_available}, margin: {margin_data}")
                    return {
                        "balances": balances,
                        "total_available_balance": total_available,
                        "margin_data": margin_data
                    }
                else:
                    logger.error(f"Ошибка получения баланса для Bybit: {data['retMsg']}")
                    raise ValueError(f"Failed to get balance: {data['retMsg']}")
            except requests.RequestException as e:
                logger.error(f"Ошибка запроса баланса для Bybit: {str(e)}")
                raise ValueError(f"Failed to fetch balance from Bybit: {str(e)}")
        elif exchange == 'binance':
            url = "https://api.binance.com/api/v3/account" if category == 'spot' else "https://fapi.binance.com/fapi/v2/account"
            timestamp = str(int(time.time() * 1000))
            params = {"timestamp": timestamp}
            query_string = urlencode(sorted(params.items()))
            signature = hmac.new(api_secret.encode('utf-8'), query_string.encode('utf-8'), hashlib.sha256).hexdigest()
            params["signature"] = signature
            headers = {"X-MBX-APIKEY": api_key}
            try:
                response = requests.get(url, headers=headers, params=params, timeout=10)
                response.raise_for_status()
                data = response.json()
                if category == 'spot':
                    balances = {
                        item['asset']: {
                            "total": safe_float(item['free']) + safe_float(item['locked']),
                            "available": safe_float(item['free'])
                        } for item in data.get('balances', []) if safe_float(item['free']) + safe_float(item['locked']) > 0
                    }
                    total_available = sum(safe_float(item['free']) for item in data.get('balances', []))
                    margin_data = {}
                else:
                    balances = {
                        asset['asset']: {
                            "total": safe_float(asset['walletBalance']),
                            "available": safe_float(asset['availableBalance'])
                        } for asset in data.get('assets', []) if safe_float(asset['walletBalance']) > 0
                    }
                    total_available = safe_float(data.get('availableBalance', "0"))
                    margin_data = {
                        'margin_balance': safe_float(data.get('totalMarginBalance', "0")),
                        'unrealized_pnl': safe_float(data.get('totalUnrealizedProfit', "0")),
                        'margin_ratio': safe_float(data.get('totalMaintMargin', "0")) / safe_float(data.get('totalMarginBalance', "1")) if safe_float(data.get('totalMarginBalance', "0")) > 0 else 0
                    }
                logger.debug(f"Баланс для Binance ({category}): total_available={total_available}, margin: {margin_data}")
                return {
                    "balances": balances,
                    "total_available_balance": total_available,
                    "margin_data": margin_data
                }
            except requests.RequestException as e:
                logger.error(f"Ошибка запроса баланса для Binance: {str(e)}")
                raise ValueError(f"Failed to fetch balance from Binance: {str(e)}")
        elif exchange == 'okx':
            url = "https://www.okx.com/api/v5/account/balance"
            timestamp = str(int(time.time()))
            method = "GET"
            request_path = "/api/v5/account/balance"
            body = ""
            sign_str = timestamp + method + request_path + body
            signature = hmac.new(api_secret.encode('utf-8'), sign_str.encode('utf-8'), hashlib.sha256).hexdigest()
            headers = {
                "OK-ACCESS-KEY": api_key,
                "OK-ACCESS-SIGN": signature,
                "OK-ACCESS-TIMESTAMP": timestamp,
                "OK-ACCESS-PASSPHRASE": ""
            }
            try:
                response = requests.get(url, headers=headers, timeout=10)
                response.raise_for_status()
                data = response.json()
                if data['code'] == '0':
                    balances = {
                        balance['ccy']: {
                            "total": safe_float(balance['bal']),
                            "available": safe_float(balance['availBal'])
                        } for balance in data['data'][0]['details'] if safe_float(balance['bal']) > 0
                    }
                    total_available = sum(safe_float(balance['availBal']) for balance in data['data'][0]['details'])
                    margin_data = {
                        'margin_balance': safe_float(data['data'][0].get('totalEq', "0")),
                        'unrealized_pnl': safe_float(data['data'][0].get('upl', "0")),
                        'margin_ratio': safe_float(data['data'][0].get('mgnRatio', "0"))
                    } if category == 'futures' else {}
                    logger.debug(f"Баланс для OKX ({category}): total_available={total_available}, margin: {margin_data}")
                    return {
                        "balances": balances,
                        "total_available_balance": total_available,
                        "margin_data": margin_data
                    }
                else:
                    logger.error(f"Ошибка получения баланса для OKX: {data['msg']}")
                    raise ValueError(f"Failed to get balance: {data['msg']}")
            except requests.RequestException as e:
                logger.error(f"Ошибка запроса баланса для OKX: {str(e)}")
                raise ValueError(f"Failed to fetch balance from OKX: {str(e)}")
        else:
            logger.error(f"Биржа {exchange} не поддерживается")
            raise ValueError(f"Unsupported exchange: {exchange}")

    @staticmethod
    @sleep_and_retry
    @limits(calls=10, period=1)
    def create_order(exchange, api_key, api_secret, symbol, side, qty, price=None, category="spot", leverage=None, margin_type=None, additional_params=None):
        """
        Создаёт ордер на бирже с учётом tickSize и basePrecision.

        Args:
            exchange (str): Название биржи ('bybit', 'binance', 'okx').
            api_key (str): API-ключ.
            api_secret (str): Секретный ключ.
            symbol (str): Торговая пара (например, 'BTCUSDT').
            side (str): Сторона ('buy' или 'sell').
            qty (float): Количество.
            price (float, optional): Цена (для лимитных ордеров).
            category (str, optional): Категория (для Bybit: 'spot' или 'linear').
            leverage (int, optional): Кредитное плечо (для фьючерсов).
            margin_type (str, optional): Тип маржи ('isolated' или 'cross').
            additional_params (dict, optional): Дополнительные параметры (например, timeInForce).

        Returns:
            dict: Информация об ордере.

        Raises:
            ValueError: Если не удалось создать ордер.
        """
        logger.info(f"Создание ордера на {exchange}: symbol={symbol}, side={side}, qty={qty}, price={price}, category={category}")
        tick_size = None
        base_precision = None
        min_order_qty = None
        if exchange == 'bybit':
            url = f"https://api.bybit.com/v5/market/instruments-info?category={'spot' if category == 'spot' else 'linear'}&symbol={symbol}"
            try:
                response = requests.get(url, timeout=10)
                response.raise_for_status()
                data = response.json()
                if data['retCode'] == 0:
                    instrument = data['result']['list'][0]
                    lot_size_filter = instrument['lotSizeFilter']
                    price_filter = instrument['priceFilter']
                    tick_size = safe_float(price_filter['tickSize'], default=0.0001)
                    base_precision = safe_float(lot_size_filter['basePrecision'], default=0.001)
                    min_order_qty = safe_float(lot_size_filter['minOrderQty'], default=0.001)
                else:
                    logger.error(f"Не удалось получить информацию о паре на Bybit: {data['retMsg']}")
                    raise ValueError(f"Не удалось получить информацию: {data['retMsg']}")
            except requests.RequestException as e:
                logger.error(f"Ошибка запроса параметров торговой пары на Bybit: {str(e)}")
                raise ValueError(f"Ошибка запроса: {str(e)}")
        elif exchange == 'binance':
            url = "https://api.binance.com/api/v3/exchangeInfo" if category == 'spot' else "https://fapi.binance.com/fapi/v1/exchangeInfo"
            try:
                response = requests.get(url, timeout=10)
                response.raise_for_status()
                data = response.json()
                for s in data['symbols']:
                    if s['symbol'] == symbol:
                        for f in s['filters']:
                            if f['filterType'] == 'PRICE_FILTER':
                                tick_size = safe_float(f['tickSize'], default=0.0001)
                            elif f['filterType'] == 'LOT_SIZE':
                                base_precision = safe_float(f['stepSize'], default=0.001)
                                min_order_qty = safe_float(f['minQty'], default=0.001)
                        break
                else:
                    logger.error(f"Торговая пара {symbol} не найдена на Binance")
                    raise ValueError(f"Торговая пара {symbol} не найдена")
            except requests.RequestException as e:
                logger.error(f"Ошибка запроса параметров торговой пары на Binance: {str(e)}")
                raise ValueError(f"Ошибка запроса: {str(e)}")
        elif exchange == 'okx':
            inst_type = 'SPOT' if category == 'spot' else 'FUTURES'
            url = f"https://www.okx.com/api/v5/public/instruments?instType={inst_type}&instId={symbol.replace('/', '-')}"
            try:
                response = requests.get(url, timeout=10)
                response.raise_for_status()
                data = response.json()
                if data['code'] == '0':
                    instrument = data['data'][0]
                    tick_size = safe_float(instrument['tickSz'], default=0.0001)
                    base_precision = safe_float(instrument['lotSz'], default=0.001)
                    min_order_qty = safe_float(instrument['minSz'], default=0.001)
                else:
                    logger.error(f"Не удалось получить информацию о паре на OKX: {data['msg']}")
                    raise ValueError(f"Не удалось получить информацию: {data['msg']}")
            except requests.RequestException as e:
                logger.error(f"Ошибка запроса параметров торговой пары на OKX: {str(e)}")
                raise ValueError(f"Ошибка запроса: {str(e)}")
        else:
            logger.error(f"Биржа {exchange} не поддерживается")
            raise ValueError(f"Биржа {exchange} не поддерживается")

        # Проверяем и корректируем qty и price
        if base_precision <= 0:
            logger.warning(f"base_precision для {symbol} равен {base_precision}, используем значение по умолчанию 0.001")
            base_precision = 0.001
        if tick_size <= 0:
            logger.warning(f"tick_size для {symbol} равен {tick_size}, используем значение по умолчанию 0.0001")
            tick_size = 0.0001
        if min_order_qty <= 0:
            logger.warning(f"min_order_qty для {symbol} равен {min_order_qty}, используем значение по умолчанию 0.001")
            min_order_qty = 0.001

        qty = math.floor(qty / base_precision) * base_precision
        if qty < min_order_qty:
            logger.error(f"Объём {qty} меньше минимального {min_order_qty} для {symbol}")
            raise ValueError(f"Объём {qty} меньше минимального {min_order_qty}")
        if price is not None:
            price = math.floor(price / tick_size) * tick_size

        formatted_qty = "{:.8f}".format(qty).rstrip('0').rstrip('.')
        formatted_price = "{:.8f}".format(price).rstrip('0').rstrip('.') if price is not None else None

        if exchange == 'bybit':
            url = "https://api.bybit.com/v5/order/create"
            timestamp = str(get_bybit_server_time())
            recv_window = "5000"
            order_params = {
                "category": category,
                "symbol": symbol,
                "side": side.capitalize(),
                "orderType": "Limit" if price else "Market",
                "qty": formatted_qty,
                "timeInForce": additional_params.get("timeInForce", "GTC") if additional_params else "GTC"
            }
            if price:
                order_params["price"] = formatted_price
            if leverage and category == 'linear':
                order_params["leverage"] = str(leverage)
            if margin_type and category == 'linear':
                order_params["marginMode"] = margin_type.upper()
            if additional_params:
                order_params.update(additional_params)
            payload = json.dumps(order_params, separators=(',', ':'), sort_keys=True)
            sign_str = timestamp + api_key + recv_window + payload
            signature = hmac.new(api_secret.encode('utf-8'), sign_str.encode('utf-8'), hashlib.sha256).hexdigest()
            headers = {
                "X-BAPI-API-KEY": api_key,
                "X-BAPI-TIMESTAMP": timestamp,
                "X-BAPI-RECV-WINDOW": recv_window,
                "X-BAPI-SIGN": signature,
                "Content-Type": "application/json"
            }
            try:
                response = requests.post(url, headers=headers, data=payload, timeout=10)
                response.raise_for_status()
                data = response.json()
                if data['retCode'] == 0:
                    logger.info(f"Ордер успешно создан на Bybit: orderId={data['result']['orderId']}")
                    return data['result']
                else:
                    logger.error(f"Ошибка создания ордера на Bybit: {data['retMsg']}")
                    raise ValueError(f"Failed to create order: {data['retMsg']}")
            except requests.RequestException as e:
                logger.error(f"Ошибка запроса создания ордера на Bybit: {str(e)}")
                raise ValueError(f"Failed to create order on Bybit: {str(e)}")
        elif exchange == 'binance':
            url = "https://api.binance.com/api/v3/order" if category == 'spot' else "https://fapi.binance.com/fapi/v1/order"
            timestamp = str(int(time.time() * 1000))
            params = {
                "symbol": symbol,
                "side": side.upper(),
                "type": "LIMIT" if price else "MARKET",
                "quantity": formatted_qty,
                "timeInForce": additional_params.get("timeInForce", "GTC") if additional_params else "GTC",
                "timestamp": timestamp
            }
            if price:
                params["price"] = formatted_price
            if leverage and category == 'futures':
                # Устанавливаем кредитное плечо
                leverage_url = "https://fapi.binance.com/fapi/v1/leverage"
                leverage_params = {
                    "symbol": symbol,
                    "leverage": leverage,
                    "timestamp": timestamp
                }
                query_string = urlencode(sorted(leverage_params.items()))
                signature = hmac.new(api_secret.encode('utf-8'), query_string.encode('utf-8'), hashlib.sha256).hexdigest()
                leverage_params["signature"] = signature
                headers = {"X-MBX-APIKEY": api_key}
                try:
                    response = requests.post(leverage_url, headers=headers, params=leverage_params, timeout=10)
                    response.raise_for_status()
                    logger.info(f"Установлено кредитное плечо {leverage} для {symbol} на Binance")
                except requests.RequestException as e:
                    logger.error(f"Ошибка установки кредитного плеча на Binance: {str(e)}")
                    raise ValueError(f"Failed to set leverage: {str(e)}")
            if additional_params:
                params.update(additional_params)
            query_string = urlencode(sorted(params.items()))
            signature = hmac.new(api_secret.encode('utf-8'), query_string.encode('utf-8'), hashlib.sha256).hexdigest()
            params["signature"] = signature
            headers = {"X-MBX-APIKEY": api_key}
            try:
                response = requests.post(url, headers=headers, params=params, timeout=10)
                response.raise_for_status()
                data = response.json()
                logger.info(f"Ордер успешно создан на Binance: orderId={data['orderId']}")
                return {"orderId": str(data["orderId"])}
            except requests.RequestException as e:
                logger.error(f"Ошибка запроса создания ордера на Binance: {str(e)}")
                raise ValueError(f"Failed to create order on Binance: {str(e)}")
        elif exchange == 'okx':
            url = "https://www.okx.com/api/v5/trade/order"
            timestamp = str(int(time.time()))
            method = "POST"
            request_path = "/api/v5/trade/order"
            order_params = {
                "instId": symbol.replace('/', '-'),
                "tdMode": "isolated" if margin_type == 'isolated' else "cross",
                "side": side,
                "ordType": "limit" if price else "market",
                "sz": formatted_qty
            }
            if price:
                order_params["px"] = formatted_price
            if leverage and category == 'futures':
                order_params["lever"] = str(leverage)
            if additional_params:
                order_params.update(additional_params)
            body = json.dumps(order_params)
            sign_str = timestamp + method + request_path + body
            signature = hmac.new(api_secret.encode('utf-8'), sign_str.encode('utf-8'), hashlib.sha256).hexdigest()
            headers = {
                "OK-ACCESS-KEY": api_key,
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
                    logger.info(f"Ордер успешно создан на OKX: ordId={data['data'][0]['ordId']}")
                    return {"orderId": data['data'][0]['ordId']}
                else:
                    logger.error(f"Ошибка создания ордера на OKX: {data['msg']}")
                    raise ValueError(f"Failed to create order: {data['msg']}")
            except requests.RequestException as e:
                logger.error(f"Ошибка запроса создания ордера на OKX: {str(e)}")
                raise ValueError(f"Failed to create order on OKX: {str(e)}")
        else:
            logger.error(f"Биржа {exchange} не поддерживается")
            raise NotImplementedError(f"Exchange {exchange} not supported")

    @staticmethod
    @sleep_and_retry
    @limits(calls=10, period=1)
    def get_order_history(exchange, api_key, api_secret, symbol, category='spot'):
        """
        Получает историю ордеров.

        Args:
            exchange (str): Название биржи ('bybit', 'binance', 'okx').
            api_key (str): API-ключ.
            api_secret (str): Секретный ключ.
            symbol (str): Торговая пара.
            category (str): Категория ('spot' или 'futures').

        Returns:
            list: Список ордеров.

        Raises:
            ValueError: Если не удалось получить историю ордеров.
        """
        logger.info(f"Получение истории ордеров для {exchange}, symbol={symbol}, category={category}")
        if exchange == 'bybit':
            url = "https://api.bybit.com/v5/order/history"
            timestamp = str(get_bybit_server_time())
            recv_window = "5000"
            params = {"category": category, "symbol": symbol}
            query_string = urlencode(sorted(params.items()))
            sign_str = timestamp + api_key + recv_window + query_string
            signature = hmac.new(api_secret.encode('utf-8'), sign_str.encode('utf-8'), hashlib.sha256).hexdigest()
            headers = {
                "X-BAPI-API-KEY": api_key,
                "X-BAPI-TIMESTAMP": timestamp,
                "X-BAPI-RECV-WINDOW": recv_window,
                "X-BAPI-SIGN": signature,
            }
            try:
                response = requests.get(url, headers=headers, params=params, timeout=10)
                response.raise_for_status()
                data = response.json()
                if data['retCode'] == 0:
                    logger.debug(f"Получена история ордеров для Bybit: {len(data['result']['list'])} записей")
                    return data['result']['list']
                else:
                    logger.error(f"Ошибка получения истории ордеров для Bybit: {data['retMsg']}")
                    raise ValueError(f"Failed to get order history: {data['retMsg']}")
            except requests.RequestException as e:
                logger.error(f"Ошибка запроса истории ордеров для Bybit: {str(e)}")
                raise ValueError(f"Failed to retrieve order history from Bybit: {str(e)}")
        elif exchange == 'binance':
            url = "https://api.binance.com/api/v3/allOrders" if category == 'spot' else "https://fapi.binance.com/fapi/v1/allOrders"
            timestamp = str(int(time.time() * 1000))
            params = {"symbol": symbol, "timestamp": timestamp}
            query_string = urlencode(sorted(params.items()))
            signature = hmac.new(api_secret.encode('utf-8'), query_string.encode('utf-8'), hashlib.sha256).hexdigest()
            params["signature"] = signature
            headers = {"X-MBX-APIKEY": api_key}
            try:
                response = requests.get(url, headers=headers, params=params, timeout=10)
                response.raise_for_status()
                data = response.json()
                logger.debug(f"Получена история ордеров для Binance: {len(data)} записей")
                return data
            except requests.RequestException as e:
                logger.error(f"Ошибка запроса истории ордеров для Binance: {str(e)}")
                raise ValueError(f"Failed to retrieve order history from Binance: {str(e)}")
        elif exchange == 'okx':
            url = "https://www.okx.com/api/v5/trade/orders-history"
            timestamp = str(int(time.time()))
            method = "GET"
            request_path = f"/api/v5/trade/orders-history?instType={'SPOT' if category == 'spot' else 'FUTURES'}"
            body = ""
            sign_str = timestamp + method + request_path + body
            signature = hmac.new(api_secret.encode('utf-8'), sign_str.encode('utf-8'), hashlib.sha256).hexdigest()
            headers = {
                "OK-ACCESS-KEY": api_key,
                "OK-ACCESS-SIGN": signature,
                "OK-ACCESS-TIMESTAMP": timestamp,
                "OK-ACCESS-PASSPHRASE": ""
            }
            params = {"instType": "SPOT" if category == 'spot' else "FUTURES"}
            try:
                response = requests.get(url, headers=headers, params=params, timeout=10)
                response.raise_for_status()
                data = response.json()
                if data['code'] == '0':
                    orders = [order for order in data['data'] if order['instId'] == symbol.replace('/', '-')]
                    logger.debug(f"Получена история ордеров для OKX: {len(orders)} записей")
                    return orders
                else:
                    logger.error(f"Ошибка получения истории ордеров для OKX: {data['msg']}")
                    raise ValueError(f"Failed to get order history: {data['msg']}")
            except requests.RequestException as e:
                logger.error(f"Ошибка запроса истории ордеров для OKX: {str(e)}")
                raise ValueError(f"Failed to retrieve order history from OKX: {str(e)}")
        else:
            logger.error(f"Биржа {exchange} не поддерживается")
            raise NotImplementedError(f"Exchange {exchange} not supported")

    @staticmethod
    @sleep_and_retry
    @limits(calls=10, period=1)
    def get_klines(exchange, symbol, interval, limit=100, category='spot'):
        """
        Получает исторические свечи для указанной торговой пары.

        Args:
            exchange (str): Название биржи ('bybit', 'binance', 'okx').
            symbol (str): Торговая пара (например, 'BTCUSDT').
            interval (str): Интервал свечей (например, '1h', '1d').
            limit (int): Количество свечей.
            category (str): Категория ('spot' или 'futures').

        Returns:
            list: Список свечей.

        Raises:
            ValueError: Если не удалось получить свечи.
        """
        logger.info(f"Получение свечей для {exchange}: symbol={symbol}, interval={interval}, category={category}")
        interval_map = {
            '1m': '1', '3m': '3', '5m': '5', '15m': '15', '30m': '30',
            '1h': '60', '2h': '120', '4h': '240', '6h': '360', '12h': '720',
            '1d': 'D', '1w': 'W', '1M': 'M',
            '1 минута': '1', '3 минуты': '3', '5 минут': '5', '15 минут': '15', '30 минут': '30',
            '1 час': '60', '2 часа': '120', '4 часа': '240', '6 часов': '360', '12 часов': '720',
            '1 день': 'D', '1 неделя': 'W', '1 месяц': 'M',
            'D': 'D', 'W': 'W', 'M': 'M'  # Добавляем поддержку для Pivot Points
        }
        api_interval = interval_map.get(interval.lower(), interval)
        if api_interval not in interval_map.values():
            logger.error(f"Неподдерживаемый интервал {interval} для {exchange}")
            raise ValueError(f"Unsupported interval: {interval}")

        if exchange == 'bybit':
            url = f"https://api.bybit.com/v5/market/kline?category={'spot' if category == 'spot' else 'linear'}&symbol={symbol}&interval={api_interval}&limit={limit}"
            try:
                response = requests.get(url, timeout=10)
                response.raise_for_status()
                data = response.json()
                if data['retCode'] == 0:
                    klines = data['result']['list']
                    logger.debug(f"Получено {len(klines)} свечей для Bybit ({category})")
                    return klines
                else:
                    logger.error(f"Ошибка получения свечей для Bybit: {data['retMsg']}")
                    raise ValueError(f"Failed to get klines: {data['retMsg']}")
            except requests.RequestException as e:
                logger.error(f"Ошибка запроса свечей для Bybit: {str(e)}")
                raise ValueError(f"Failed to fetch klines from Bybit: {str(e)}")
        elif exchange == 'binance':
            # Binance использует немного другие обозначения интервалов
            binance_interval_map = {
                '1': '1m', '3': '3m', '5': '5m', '15': '15m', '30': '30m',
                '60': '1h', '120': '2h', '240': '4h', '360': '6h', '720': '12h',
                'D': '1d', 'W': '1w', 'M': '1M'
            }
            binance_interval = binance_interval_map.get(api_interval, api_interval)
            url = f"https://api.binance.com/api/v3/klines?symbol={symbol}&interval={binance_interval}&limit={limit}" if category == 'spot' else f"https://fapi.binance.com/fapi/v1/klines?symbol={symbol}&interval={binance_interval}&limit={limit}"
            try:
                response = requests.get(url, timeout=10)
                response.raise_for_status()
                klines = response.json()
                logger.debug(f"Получено {len(klines)} свечей для Binance ({category})")
                return klines
            except requests.RequestException as e:
                logger.error(f"Ошибка запроса свечей для Binance: {str(e)}")
                raise ValueError(f"Failed to fetch klines from Binance: {str(e)}")
        elif exchange == 'okx':
            # OKX использует формат интервалов вида "1m", "1H", "1D"
            okx_interval_map = {
                '1': '1m', '3': '3m', '5': '5m', '15': '15m', '30': '30m',
                '60': '1H', '120': '2H', '240': '4H', '360': '6H', '720': '12H',
                'D': '1D', 'W': '1W', 'M': '1M'
            }
            okx_interval = okx_interval_map.get(api_interval, api_interval)
            url = f"https://www.okx.com/api/v5/market/candles?instId={symbol.replace('/', '-')}&bar={okx_interval}&limit={limit}"
            try:
                response = requests.get(url, timeout=10)
                response.raise_for_status()
                data = response.json()
                if data['code'] == '0':
                    klines = data['data']
                    logger.debug(f"Получено {len(klines)} свечей для OKX ({category})")
                    return klines
                else:
                    logger.error(f"Ошибка получения свечей для OKX: {data['msg']}")
                    raise ValueError(f"Failed to get klines: {data['msg']}")
            except requests.RequestException as e:
                logger.error(f"Ошибка запроса свечей для OKX: {str(e)}")
                raise ValueError(f"Failed to fetch klines from OKX: {str(e)}")
        else:
            logger.error(f"Биржа {exchange} не поддерживается")
            raise NotImplementedError(f"Exchange {exchange} not supported")