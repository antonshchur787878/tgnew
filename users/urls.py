from django.urls import path
from .views import UserListView, UserRegisterView, UserLoginView, GoogleLoginView, TelegramLoginView

urlpatterns = [
    path('', UserListView.as_view(), name='user-list'),
    path('register/', UserRegisterView.as_view(), name='user-register'),
    path('login/', UserLoginView.as_view(), name='user-login'),
    path('google-login/', GoogleLoginView.as_view(), name='google-login'),
    path('telegram-login/', TelegramLoginView.as_view(), name='telegram-login'),  # Добавлен маршрут
]