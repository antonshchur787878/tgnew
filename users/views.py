from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.status import HTTP_201_CREATED, HTTP_400_BAD_REQUEST
from rest_framework_simplejwt.tokens import RefreshToken
from django.contrib.auth import authenticate, login
from .models import CustomUser
from .serializers import UserSerializer
import logging

logger = logging.getLogger(__name__)

class UserRegisterView(APIView):
    permission_classes = [AllowAny]

    def post(self, request):
        serializer = UserSerializer(data=request.data)
        if serializer.is_valid():
            user = serializer.save()
            refresh = RefreshToken.for_user(user)
            logger.info(f"Пользователь {user.username} успешно зарегистрирован")
            return Response({
                'refresh': str(refresh),
                'access': str(refresh.access_token),
                'user': serializer.data
            }, status=HTTP_201_CREATED)
        logger.error(f"Ошибка регистрации: {serializer.errors}")
        return Response(serializer.errors, status=HTTP_400_BAD_REQUEST)

class UserListView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        user = request.user
        serializer = UserSerializer(user)
        logger.info(f"Пользователь {user.username} запросил свои данные")
        return Response(serializer.data)

class UserLoginView(APIView):
    permission_classes = [AllowAny]

    def post(self, request):
        username = request.data.get('username')
        password = request.data.get('password')

        if not username or not password:
            logger.error("Отсутствуют обязательные поля при входе")
            return Response({"error": "Необходимо указать username и password"}, status=HTTP_400_BAD_REQUEST)

        user = authenticate(username=username, password=password)
        if user is not None:
            login(request, user)  # Аутентификация через Django
            refresh = RefreshToken.for_user(user)
            logger.info(f"Пользователь {username} успешно вошел")
            return Response({
                'refresh': str(refresh),
                'access': str(refresh.access_token),
            })
        logger.error(f"Неудачная попытка входа для {username}")
        return Response({"error": "Неверные учетные данные"}, status=HTTP_400_BAD_REQUEST)

class GoogleLoginView(APIView):
    permission_classes = [AllowAny]

    def post(self, request):
        if request.user.is_authenticated:
            refresh = RefreshToken.for_user(request.user)
            logger.info(f"Пользователь {request.user.username} вошел через Google")
            return Response({
                'refresh': str(refresh),
                'access': str(refresh.access_token),
            })
        logger.error("Ошибка входа через Google: пользователь не аутентифицирован")
        return Response({"error": "Ошибка аутентификации через Google"}, status=HTTP_400_BAD_REQUEST)

class TelegramLoginView(APIView):
    permission_classes = [AllowAny]

    def post(self, request):
        if request.user.is_authenticated:
            refresh = RefreshToken.for_user(request.user)
            logger.info(f"Пользователь {request.user.username} вошел через Telegram")
            return Response({
                'refresh': str(refresh),
                'access': str(refresh.access_token),
            })
        logger.error("Ошибка входа через Telegram: пользователь не аутентифицирован")
        return Response({"error": "Ошибка аутентификации через Telegram"}, status=HTTP_400_BAD_REQUEST)