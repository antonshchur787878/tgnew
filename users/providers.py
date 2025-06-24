from allauth.socialaccount.providers.base import ProviderAccount
from allauth.socialaccount.providers.oauth2.provider import OAuth2Provider

class TelegramAccount(ProviderAccount):
    def to_str(self):
        return self.account.extra_data.get('username', 'Unknown')

class TelegramProvider(OAuth2Provider):
    id = 'telegram'
    name = 'Telegram'
    account_class = TelegramAccount

    def extract_uid(self, data):
        return str(data.get('id'))

    def extract_common_fields(self, data):
        return dict(
            username=data.get('username'),
            first_name=data.get('first_name'),
            last_name=data.get('last_name'),
        )

    def get_login_url(self, request, **kwargs):
        # Используем маршрут, определенный в allauth.urls
        return super().get_login_url(request, **kwargs)

    def sociallogin_from_response(self, request, response):
        from allauth.socialaccount.models import SocialLogin
        from django.contrib.auth import get_user_model

        User = get_user_model()
        uid = str(response.get('id'))
        extra_data = response

        # Проверяем или создаем пользователя
        user = User.objects.filter(username=extra_data.get('username')).first()
        if not user:
            user = User.objects.create_user(
                username=extra_data.get('username'),
                email='',  # Email не обязателен
                first_name=extra_data.get('first_name'),
                last_name=extra_data.get('last_name')
            )
            user.telegram_id = str(response.get('id'))  # Сохраняем telegram_id
            user.save()

        # Создаем SocialLogin
        social_login = SocialLogin(user)
        social_login.state = SocialLogin.state_from_request(request)
        social_login.token = None
        social_login.account.extra_data = extra_data
        return social_login