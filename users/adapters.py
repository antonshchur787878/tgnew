from allauth.account.adapter import DefaultAccountAdapter
from allauth.socialaccount.adapter import DefaultSocialAccountAdapter
from django.contrib.auth import get_user_model, login
from django.http import HttpResponseRedirect
from rest_framework_simplejwt.tokens import RefreshToken
from django.conf import settings
import logging

User = get_user_model()
logger = logging.getLogger(__name__)

class CustomSocialAccountAdapter(DefaultSocialAccountAdapter):
    def save_user(self, request, sociallogin, form=None):
        """Сохраняем или обновляем пользователя."""
        user = super().save_user(request, sociallogin, form)
        user.email = sociallogin.account.extra_data.get('email', '')
        user.username = sociallogin.account.extra_data.get('email', '').split('@')[0] or f"user_{user.id}"
        user.set_unusable_password()
        user.save()
        logger.info(f"User {user.username} created or updated via {sociallogin.account.provider}")
        return user

    def get_login_redirect_url(self, request, sociallogin):
        """Перенаправление после успешного входа с выдачей JWT."""
        user = sociallogin.user
        login(request, user)  # Аутентификация пользователя
        refresh = RefreshToken.for_user(user)
        # Сохраняем токен в сессии для последующей передачи фронтенду
        request.session['access_token'] = str(refresh.access_token)
        request.session['refresh_token'] = str(refresh)
        logger.info(f"User {user.username} is authenticated, redirecting to /dashboard/ with JWT")
        # Возвращаем JSON-ответ с токеном вместо редиректа
        response_data = {
            'access_token': str(refresh.access_token),
            'refresh_token': str(refresh),
            'redirect_url': '/dashboard/'
        }
        return HttpResponseRedirect('/dashboard/')  # Временное решение, пока фронтенд не адаптирован

    def get_connect_redirect_url(self, request, sociallogin):
        """Перенаправление после подключения соц. аккаунта."""
        if request.user.is_authenticated:
            logger.info(f"User {request.user.username} is authenticated, redirecting to /dashboard/ from connect")
            return '/dashboard/'
        logger.info("User is not authenticated during connect")
        return super().get_connect_redirect_url(request, sociallogin)