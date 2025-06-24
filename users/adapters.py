# users/adapters.py
from allauth.account.adapter import DefaultAccountAdapter
from allauth.socialaccount.adapter import DefaultSocialAccountAdapter
from django.contrib.auth import get_user_model

User = get_user_model()

class CustomSocialAccountAdapter(DefaultSocialAccountAdapter):
    def save_user(self, request, sociallogin, form=None):
        user = super().save_user(request, sociallogin, form)
        user.email = sociallogin.account.extra_data.get('email', '')
        user.username = sociallogin.account.extra_data.get('email', '').split('@')[0] or 'user_' + str(user.id)
        user.set_unusable_password()  # Пароль не будет использоваться
        user.save()
        return user