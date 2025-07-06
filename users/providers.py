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

    def get_default_scope(self):
        return []

    def get_auth_params(self, request, action):
        return {'bot_id': self.get_app(self.request).key}