from django.test import TestCase
from django.contrib.auth.models import User
from rest_framework.test import APIClient
from .models import APIKey, Bot, BotSettings, BotPosition
from .serializers import APIKeySerializer, BotSerializer, BotSettingsSerializer, BotStatusSerializer
from .strategies import TradingStrategy
from unittest.mock import patch
import json
import logging

logger = logging.getLogger(__name__)

class APITests(TestCase):
    def setUp(self):
        """
        Настройка тестового окружения: создаём пользователя и аутентифицируем клиента.
        """
        self.user = User.objects.create_user(username='testuser', password='testpass')
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)
        logger.info("Тестовое окружение настроено: пользователь создан и аутентифицирован")

    def test_create_api_key(self):
        """
        Тест создания API-ключа.
        """
        data = {
            'exchange': 'bybit',
            'api_key': 'test_api_key',
            'api_secret': 'test_api_secret'
        }
        with patch('requests.get') as mock_get:
            mock_get.return_value.status_code = 200
            mock_get.return_value.json.return_value = {'retCode': 0}
            serializer = APIKeySerializer(data=data, context={'request': self.client.request(user=self.user)})
            self.assertTrue(serializer.is_valid(), serializer.errors)
            api_key = serializer.save()
            self.assertEqual(api_key.exchange, 'bybit')
            self.assertTrue(api_key.api_key.startswith('enc:'), "API-ключ должен быть зашифрован")
            self.assertTrue(api_key.api_secret.startswith('enc:'), "API-секрет должен быть зашифрован")
            logger.info("Тест создания API-ключа успешно пройден")

    def test_create_bot(self):
        """
        Тест создания бота.
        """
        api_key = APIKey.objects.create(
            user=self.user,
            exchange='bybit',
            api_key='enc:test_api_key',
            api_secret='enc:test_api_secret'
        )
        data = {
            'api_key_id': api_key.id,
            'strategy': 'spot',
            'algorithm': 'long',
            'trading_pair': 'BTCUSDT',
            'deposit': 1000,
            'trade_mode': 'order_grid',
            'additional_settings': {'base_quantity': 0.1},
            'settings': {
                'signal_type': 'rsi',
                'signal_params': {'period': 14, 'threshold': 30},
                'signal_interval': '1h',
                'take_profit': 2.0,
                'grid_orders': 5,
                'grid_spacing': 1.0,
                'grid_overlap': 20.0,
                'martingale': 1.5,
                'logarithmic_distribution': False,
                'partial_grid': False,
                'grid_follow': False,
                'stop_after_deals': False,
                'preset': 'moderate'
            }
        }
        with patch('requests.get') as mock_get:
            # Мокаем запросы для проверки торговой пары и баланса
            mock_get.side_effect = [
                # get_trading_pairs
                type('Response', (), {'status_code': 200, 'json': lambda: {
                    'retCode': 0,
                    'result': {'list': [{'symbol': 'BTCUSDT'}]}
                }})(),
                # get_balance
                type('Response', (), {'status_code': 200, 'json': lambda: {
                    'retCode': 0,
                    'result': {'list': [{'totalAvailableBalance': '5000'}]}
                }})()
            ]
            serializer = BotSerializer(data=data, context={'request': self.client.request(user=self.user)})
            self.assertTrue(serializer.is_valid(), serializer.errors)
            bot = serializer.save()
            self.assertEqual(bot.trading_pair, 'BTCUSDT')
            self.assertEqual(bot.settings.signal_type, 'rsi')
            self.assertEqual(bot.additional_settings['base_quantity'], 0.1)
            self.assertEqual(bot.strategy, 'spot')
            self.assertEqual(bot.algorithm, 'long')
            logger.info("Тест создания бота успешно пройден")

    def test_signal_params_validation(self):
        """
        Тест валидации signal_params для разных типов сигналов.
        """
        # Тест для RSI с некорректным threshold
        data = {
            'signal_type': 'rsi',
            'signal_params': {'period': 14, 'threshold': -10},  # Неверный threshold
            'signal_interval': '1h',
            'take_profit': 2.0,
            'grid_orders': 5,
            'grid_spacing': 1.0,
            'grid_overlap': 20.0,
            'martingale': 1.5,
            'logarithmic_distribution': False,
            'partial_grid': False,
            'grid_follow': False,
            'stop_after_deals': False
        }
        serializer = BotSettingsSerializer(data=data)
        self.assertFalse(serializer.is_valid())
        self.assertIn('signal_params', serializer.errors)
        self.assertIn('threshold должен быть числом в диапазоне 0–100', str(serializer.errors['signal_params']))
        logger.info("Тест валидации signal_params для RSI (негативный threshold) пройден")

        # Тест для RSI с отсутствующим threshold
        data['signal_params'] = {'period': 14}  # Отсутствует threshold
        serializer = BotSettingsSerializer(data=data)
        self.assertFalse(serializer.is_valid())
        self.assertIn('signal_params', serializer.errors)
        self.assertIn("Отсутствует обязательное поле 'threshold'", str(serializer.errors['signal_params']))
        logger.info("Тест валидации signal_params для RSI (отсутствие threshold) пройден")

        # Тест для base_volume
        data['signal_type'] = 'base_volume'
        data['signal_params'] = {'threshold': 1000}
        serializer = BotSettingsSerializer(data=data)
        self.assertTrue(serializer.is_valid(), serializer.errors)
        logger.info("Тест валидации signal_params для base_volume пройден")

        # Тест для price с некорректным target_price
        data['signal_type'] = 'price'
        data['signal_params'] = {'target_price': -500}
        serializer = BotSettingsSerializer(data=data)
        self.assertFalse(serializer.is_valid())
        self.assertIn('signal_params', serializer.errors)
        self.assertIn('target_price должен быть положительным числом', str(serializer.errors['signal_params']))
        logger.info("Тест валидации signal_params для price (негативный target_price) пройден")

    def test_signal_interval_validation(self):
        """
        Тест валидации signal_interval.
        """
        data = {
            'signal_type': 'rsi',
            'signal_params': {'period': 14, 'threshold': 30},
            'signal_interval': 'invalid_interval',  # Неверный интервал
            'take_profit': 2.0,
            'grid_orders': 5,
            'grid_spacing': 1.0,
            'grid_overlap': 20.0,
            'martingale': 1.5,
            'logarithmic_distribution': False,
            'partial_grid': False,
            'grid_follow': False,
            'stop_after_deals': False
        }
        serializer = BotSettingsSerializer(data=data)
        self.assertFalse(serializer.is_valid())
        self.assertIn('signal_interval', serializer.errors)
        logger.info("Тест валидации signal_interval (некорректный интервал) пройден")

        data['signal_interval'] = '1m'  # Корректный интервал
        serializer = BotSettingsSerializer(data=data)
        self.assertTrue(serializer.is_valid(), serializer.errors)
        logger.info("Тест валидации signal_interval (корректный интервал) пройден")

        data['signal_interval'] = '1 минута'  # Текстовый формат интервала
        serializer = BotSettingsSerializer(data=data)
        self.assertTrue(serializer.is_valid(), serializer.errors)
        logger.info("Тест валидации signal_interval (текстовый формат) пройден")

    def test_bot_status_update(self):
        """
        Тест обновления статуса бота через BotStatusSerializer.
        """
        api_key = APIKey.objects.create(
            user=self.user,
            exchange='bybit',
            api_key='enc:test_api_key',
            api_secret='enc:test_api_secret'
        )
        bot_settings = BotSettings.objects.create(
            signal_type='rsi',
            signal_params={'period': 14, 'threshold': 30},
            signal_interval='1h',
            take_profit=2.0,
            grid_orders=5,
            grid_spacing=1.0,
            grid_overlap=20.0,
            martingale=1.5,
            logarithmic_distribution=False,
            partial_grid=False,
            grid_follow=False,
            stop_after_deals=False
        )
        bot = Bot.objects.create(
            user=self.user,
            name='Test Bot',
            api_key=api_key,
            trading_pair='BTCUSDT',
            deposit=1000,
            trade_mode='order_grid',
            additional_settings={'base_quantity': 0.1},
            settings=bot_settings,
            status='active',
            is_running=True
        )
        data = {
            'status': 'stopped',
            'is_running': False
        }
        with patch('bots.strategies.stop_bot.delay') as mock_stop_bot:
            serializer = BotStatusSerializer(instance=bot, data=data)
            self.assertTrue(serializer.is_valid(), serializer.errors)
            updated_bot = serializer.save()
            self.assertEqual(updated_bot.status, 'stopped')
            self.assertFalse(updated_bot.is_running)
            mock_stop_bot.assert_called_once_with(bot.id)
            logger.info("Тест обновления статуса бота успешно пройден")

class TradingStrategyTests(TestCase):
    def setUp(self):
        """
        Настройка тестового окружения для TradingStrategy.
        """
        self.user = User.objects.create_user(username='testuser', password='testpass')
        self.api_key = APIKey.objects.create(
            user=self.user,
            exchange='bybit',
            api_key='enc:test_api_key',
            api_secret='enc:test_api_secret'
        )
        self.bot_settings = BotSettings.objects.create(
            signal_type='rsi',
            signal_params={'period': 14, 'threshold': 30},
            signal_interval='1h',
            take_profit=2.0,
            grid_orders=5,
            grid_spacing=1.0,
            grid_overlap=20.0,
            martingale=1.5,
            logarithmic_distribution=False,
            partial_grid=False,
            grid_follow=False,
            stop_after_deals=False
        )
        self.bot = Bot.objects.create(
            user=self.user,
            name='Test Bot',
            api_key=self.api_key,
            trading_pair='BTCUSDT',
            deposit=1000,
            trade_mode='order_grid',
            additional_settings={'base_quantity': 0.1},
            settings=self.bot_settings,
            status='active',
            is_running=True
        )
        self.strategy = TradingStrategy(self.bot)
        logger.info("Тестовое окружение для TradingStrategy настроено")

    def test_calculate_quantity_qty_step(self):
        """
        Тест округления количества с учётом basePrecision.
        """
        with patch('requests.get') as mock_get:
            mock_get.return_value.status_code = 200
            mock_get.return_value.json.return_value = {
                'retCode': 0,
                'result': {
                    'list': [{
                        'lotSizeFilter': {
                            'basePrecision': '0.001',
                            'minOrderQty': '0.001'
                        }
                    }]
                }
            }
            qty = self.strategy.calculate_quantity(level_index=0)
            self.assertEqual(qty, 0.1)  # base_quantity = 0.1
            self.assertTrue(qty % 0.001 == 0, "Количество должно быть кратно basePrecision")
            logger.info("Тест округления количества успешно пройден")

    def test_place_order_qty_step_error(self):
        """
        Тест ошибки при некорректном количестве и успешного размещения ордера.
        """
        with patch('requests.get') as mock_get, patch('requests.post') as mock_post:
            # Информация о торговой паре
            mock_get.return_value.status_code = 200
            mock_get.return_value.json.return_value = {
                'retCode': 0,
                'result': {
                    'list': [{
                        'lotSizeFilter': {
                            'basePrecision': '0.001',
                            'minOrderQty': '0.002'  # Минимальное количество больше, чем qty
                        },
                        'priceFilter': {
                            'tickSize': '0.01'
                        }
                    }]
                }
            }
            # Проверяем, что слишком маленькое количество вызовет ошибку
            with self.assertRaises(ValueError) as context:
                self.strategy.place_order('buy', 50000.0, 0.001)
            self.assertIn("меньше минимального", str(context.exception))
            logger.info("Тест ошибки при некорректном количестве пройден")

            # Исправляем количество и проверяем успешное создание ордера
            mock_get.return_value.json.return_value['result']['list'][0]['lotSizeFilter']['minOrderQty'] = '0.001'
            mock_post.return_value.status_code = 200
            mock_post.return_value.json.return_value = {
                'retCode': 0,
                'result': {'orderId': '12345'}
            }
            result = self.strategy.place_order('buy', 50000.0, 0.1)
            self.assertEqual(result['orderId'], '12345')
            self.assertTrue(0.1 % 0.001 == 0, "Количество должно быть кратно basePrecision")
            logger.info("Тест успешного размещения ордера пройден")

    def test_check_signal_empty_data(self):
        """
        Тест обработки пустых данных в check_signal.
        """
        with patch('requests.get') as mock_get:
            mock_get.return_value.status_code = 200
            mock_get.return_value.json.return_value = {
                'retCode': 0,
                'result': {'list': []}
            }
            result = self.strategy.check_signal()
            self.assertFalse(result)
            logger.info("Тест обработки пустых данных в check_signal пройден")

    def test_check_signal_with_data(self):
        """
        Тест проверки сигнала с данными.
        """
        with patch('requests.get') as mock_get:
            mock_get.side_effect = [
                # Запрос исторических данных
                type('Response', (), {'status_code': 200, 'json': lambda: {
                    'retCode': 0,
                    'result': {
                        'list': [
                            {'close': 50000, 'timestamp': 1234567890},
                            {'close': 49000, 'timestamp': 1234567800}
                        ]
                    }
                }})(),
                # Запрос текущей цены (для расчёта индикатора)
                type('Response', (), {'status_code': 200, 'json': lambda: {
                    'retCode': 0,
                    'result': {'last': 51000}
                }})()
            ]
            result = self.strategy.check_signal()
            self.assertIn(result, [True, False], "Результат должен быть булевым")
            logger.info("Тест проверки сигнала с данными пройден")