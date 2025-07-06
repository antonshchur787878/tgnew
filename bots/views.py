from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from rest_framework.pagination import PageNumberPagination
from rest_framework.permissions import IsAuthenticated
from rest_framework.throttling import UserRateThrottle
from django.core.exceptions import PermissionDenied
import logging
from .models import APIKey, Bot, LogEntry
from .serializers import APIKeySerializer, BotSerializer
from .utils import ExchangeAPI
from .tasks import run_trading_strategy, stop_bot, log_action
from django.http import JsonResponse, HttpResponse  # Добавлен импорт HttpResponse
from rest_framework_simplejwt.tokens import RefreshToken
from allauth.socialaccount.models import SocialAccount
from django.contrib.auth import get_user_model
from django.shortcuts import redirect

logger = logging.getLogger(__name__)

User = get_user_model()

class StandardResultsSetPagination(PageNumberPagination):
    """
    Пагинация для списков объектов.
    """
    page_size = 10
    page_size_query_param = 'page_size'
    max_page_size = 100

class APIKeyListView(APIView):
    """
    API для управления API-ключами пользователя.
    """
    permission_classes = [IsAuthenticated]
    pagination_class = StandardResultsSetPagination

    def get(self, request):
        """
        Получает список API-ключей пользователя.
        """
        logger.info(f"Пользователь {request.user.username} запрашивает список API-ключей")
        api_keys = APIKey.objects.filter(user=request.user)
        serializer = APIKeySerializer(api_keys, many=True)
        return Response(serializer.data)

    def post(self, request):
        """
        Создаёт новый API-ключ.
        """
        logger.info(f"Пользователь {request.user.username} создаёт новый API-ключ")
        serializer = APIKeySerializer(data=request.data, context={'request': request})
        if serializer.is_valid():
            serializer.save(user=request.user)
            log_action.delay(
                user_id=request.user.id,
                bot_id=None,
                action="API key created",
                details=f"API key for {serializer.validated_data['exchange']} created",
                status="success"
            )
            return Response(serializer.data, status=status.HTTP_201_CREATED)
        logger.error(f"Ошибка создания API-ключа: {serializer.errors}")
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

class APIKeyDeleteView(APIView):
    """
    API для удаления API-ключа.
    """
    permission_classes = [IsAuthenticated]

    def delete(self, request, pk):
        """
        Удаляет API-ключ.
        """
        logger.info(f"Пользователь {request.user.username} пытается удалить API-ключ ID {pk}")
        try:
            api_key = APIKey.objects.get(pk=pk, user=request.user)
            api_key.delete()
            log_action.delay(
                user_id=request.user.id,
                bot_id=None,
                action="API key deleted",
                details=f"API key ID {pk} deleted",
                status="success"
            )
            return Response(status=status.HTTP_204_NO_CONTENT)
        except APIKey.DoesNotExist:
            logger.error(f"API-ключ ID {pk} не найден для пользователя {request.user.username}")
            return Response({"error": "API key not found"}, status=status.HTTP_404_NOT_FOUND)

class BotListCreateView(APIView):
    """
    API для создания и получения списка ботов.
    """
    permission_classes = [IsAuthenticated]
    pagination_class = StandardResultsSetPagination

    def get(self, request):
        """
        Получает список ботов пользователя.
        """
        logger.info(f"Пользователь {request.user.username} запрашивает список ботов")
        bots = Bot.objects.filter(user=request.user).select_related('api_key', 'settings')
        paginator = self.pagination_class()
        page = paginator.paginate_queryset(bots, request)
        serializer = BotSerializer(page, many=True)
        return paginator.get_paginated_response(serializer.data)

    def post(self, request):
        """
        Создаёт нового бота.
        """
        logger.info(f"Пользователь {request.user.username} создаёт нового бота")
        serializer = BotSerializer(data=request.data, context={'request': request})
        if serializer.is_valid():
            bot = serializer.save(user=request.user)
            log_action.delay(
                user_id=request.user.id,
                bot_id=bot.id,
                action="Bot created",
                details=f"Bot {bot.name} created with strategy {bot.strategy}",
                status="success"
            )
            return Response(serializer.data, status=status.HTTP_201_CREATED)
        logger.error(f"Ошибка создания бота: {serializer.errors}")
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

class BotDetailView(APIView):
    """
    API для управления конкретным ботом.
    """
    permission_classes = [IsAuthenticated]

    def get_object(self, pk, user):
        """
        Получает бота по ID и проверяет права доступа.
        """
        try:
            return Bot.objects.select_related('api_key', 'settings').get(pk=pk, user=user)
        except Bot.DoesNotExist:
            logger.error(f"Бот ID {pk} не найден для пользователя {user.username}")
            raise PermissionDenied("Bot not found or you do not have permission to access it")

    def get(self, request, pk):
        """
        Получает информацию о боте.
        """
        logger.info(f"Пользователь {request.user.username} запрашивает информацию о боте ID {pk}")
        bot = self.get_object(pk, request.user)
        serializer = BotSerializer(bot)
        return Response(serializer.data)

    def put(self, request, pk):
        """
        Обновляет бота.
        """
        logger.info(f"Пользователь {request.user.username} обновляет бота ID {pk}")
        bot = self.get_object(pk, request.user)
        serializer = BotSerializer(bot, data=request.data, context={'request': request})
        if serializer.is_valid():
            serializer.save()
            log_action.delay(
                user_id=request.user.id,
                bot_id=bot.id,
                action="Bot updated",
                details=f"Bot {bot.name} updated",
                status="success"
            )
            return Response(serializer.data)
        logger.error(f"Ошибка обновления бота: {serializer.errors}")
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

    def delete(self, request, pk):
        """
        Удаляет бота.
        """
        logger.info(f"Пользователь {request.user.username} удаляет бота ID {pk}")
        bot = self.get_object(pk, request.user)
        bot.delete()
        log_action.delay(
            user_id=request.user.id,
            bot_id=pk,
            action="Bot deleted",
            details=f"Bot ID {pk} deleted",
            status="success"
        )
        return Response(status=status.HTTP_204_NO_CONTENT)

class BotStartView(APIView):
    """
    API для запуска бота.
    """
    permission_classes = [IsAuthenticated]

    def get_object(self, pk, user):
        """
        Получает бота по ID и проверяет права доступа.
        """
        try:
            return Bot.objects.get(pk=pk, user=user)
        except Bot.DoesNotExist:
            logger.error(f"Бот ID {pk} не найден для пользователя {user.username}")
            raise PermissionDenied("Bot not found or you do not have permission to access it")

    def post(self, request, pk):
        """
        Запускает бота.
        """
        logger.info(f"Пользователь {request.user.username} запускает бота ID {pk}")
        bot = self.get_object(pk, request.user)
        if bot.status == 'active':
            return Response({"message": "Bot is already running"}, status=status.HTTP_400_BAD_REQUEST)

        bot.status = 'active'
        bot.is_running = True
        bot.save()
        run_trading_strategy.delay(bot.id)
        log_action.delay(
            user_id=request.user.id,
            bot_id=bot.id,
            action="Bot started",
            details=f"Bot {bot.name} started",
            status="success"
        )
        return Response({"message": "Bot started successfully"})

class BotStopView(APIView):
    """
    API для остановки бота.
    """
    permission_classes = [IsAuthenticated]

    def get_object(self, pk, user):
        """
        Получает бота по ID и проверяет права доступа.
        """
        try:
            return Bot.objects.get(pk=pk, user=user)
        except Bot.DoesNotExist:
            logger.error(f"Бот ID {pk} не найден для пользователя {user.username}")
            raise PermissionDenied("Bot not found or you do not have permission to access it")

    def post(self, request, pk):
        """
        Останавливает бота.
        """
        logger.info(f"Пользователь {request.user.username} останавливает бота ID {pk}")
        bot = self.get_object(pk, request.user)
        if bot.status != 'active':
            return Response({"message": "Bot is not running"}, status=status.HTTP_400_BAD_REQUEST)

        stop_bot.delay(bot.id)
        return Response({"message": "Bot stop initiated"})

class TestOrderThrottle(UserRateThrottle):
    rate = '10/hour'  # Ограничение: 10 тестовых ордеров в час

class BotTestOrderView(APIView):
    """
    API для создания тестового ордера.
    """
    permission_classes = [IsAuthenticated]
    throttle_classes = [TestOrderThrottle]

    def get_object(self, pk, user):
        """
        Получает бота по ID и проверяет права доступа.
        """
        try:
            return Bot.objects.select_related('api_key').get(pk=pk, user=user)
        except Bot.DoesNotExist:
            logger.error(f"Бот ID {pk} не найден для пользователя {user.username}")
            raise PermissionDenied("Bot not found or you do not have permission to access it")

    def post(self, request, pk):
        """
        Создаёт тестовый ордер.
        """
        logger.info(f"Пользователь {request.user.username} создаёт тестовый ордер для бота ID {pk}")
        bot = self.get_object(pk, request.user)
        decrypted_keys = bot.api_key.get_decrypted_keys()
        try:
            result = ExchangeAPI.create_order(
                exchange=bot.api_key.exchange,
                api_key=decrypted_keys['api_key'],
                api_secret=decrypted_keys['api_secret'],
                symbol=bot.trading_pair.replace('/', ''),
                side="buy",
                qty=0.001,  # Минимальное количество для теста
                price=None
            )
            log_action.delay(
                user_id=request.user.id,
                bot_id=bot.id,
                action="Test order created",
                details=f"Test order created for {bot.trading_pair}: {result}",
                status="success"
            )
            return Response(result)
        except Exception as e:
            logger.error(f"Ошибка создания тестового ордера для бота ID {pk}: {str(e)}")
            log_action.delay(
                user_id=request.user.id,
                bot_id=bot.id,
                action="Test order failed",
                details=f"Failed to create test order: {str(e)}",
                status="error",
                error_message=str(e)
            )
            return Response({"error": str(e)}, status=status.HTTP_400_BAD_REQUEST)

class BotStatusView(APIView):
    """
    API для проверки статуса бота.
    """
    permission_classes = [IsAuthenticated]

    def get(self, request, pk):
        """
        Получает статус бота.
        """
        logger.info(f"Пользователь {request.user.username} запрашивает статус бота ID {pk}")
        try:
            bot = Bot.objects.get(pk=pk)
            if bot.user != request.user:
                logger.warning(f"Пользователь {request.user.username} не имеет доступа к боту ID {pk}")
                return Response({"detail": "You do not have permission to view this bot."}, status=status.HTTP_403_FORBIDDEN)
            return Response({
                "id": bot.id,
                "name": bot.name,
                "is_running": bot.is_running,
                "status": bot.status,
                "trading_pair": bot.trading_pair,
                "trade_mode": bot.trade_mode
            })
        except Bot.DoesNotExist:
            logger.error(f"Бот ID {pk} не найден для пользователя {request.user.username}")
            return Response({"detail": "Bot not found."}, status=status.HTTP_404_NOT_FOUND)

def test_error(request):
    """
    Представление для тестирования ошибок (например, для проверки Sentry).
    """
    logger.info("Тестирование ошибки через маршрут test-error/")
    raise Exception("Это тестовая ошибка для проверки обработки ошибок")

def telegram_login(request):
    """
    Заглушка для обработки авторизации через Telegram.
    Требуется доработка с использованием social_django или кастомной логики.
    """
    logger.info(f"Пользователь пытается войти через Telegram: {request.GET}")
    return JsonResponse({'status': 'Telegram login not implemented yet'}, status=501)

def telegram_login_test(request):
    """
    Тестовый маршрут для отладки авторизации через Telegram.
    """
    logger.info(f"Тестовый маршрут Telegram login: {request.GET}")
    return HttpResponse("Telegram login test page. Please ensure your Telegram OAuth setup is correct.")