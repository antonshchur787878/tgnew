from django.urls import path
from .views import UserListView, UserRegisterView, UserLoginView

urlpatterns = [
    path('', UserListView.as_view(), name='user-list'),
    path('register/', UserRegisterView.as_view(), name='user-register'),
    path('login/', UserLoginView.as_view(), name='user-login'),
]