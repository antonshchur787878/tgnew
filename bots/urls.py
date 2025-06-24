from django.urls import path
from .views import (
    APIKeyListView, APIKeyDeleteView,
    BotListCreateView, BotDetailView,
    BotStartView, BotStopView,
    BotTestOrderView, BotOrderHistoryView,
    BotStatusView  # Добавляем новый view
)

urlpatterns = [
    path('api-keys/', APIKeyListView.as_view(), name='api-key-list'),
    path('api-keys/<int:pk>/delete/', APIKeyDeleteView.as_view(), name='api-key-delete'),
    path('bots/', BotListCreateView.as_view(), name='bot-list'),
    path('bots/<int:pk>/', BotDetailView.as_view(), name='bot-detail'),
    path('bots/<int:pk>/start/', BotStartView.as_view(), name='bot-start'),
    path('bots/<int:pk>/stop/', BotStopView.as_view(), name='bot-stop'),
    path('bots/<int:pk>/test-order/', BotTestOrderView.as_view(), name='bot-test-order'),
    path('bots/<int:pk>/orders/', BotOrderHistoryView.as_view(), name='bot-order-history'),
    path('bots/<int:pk>/status/', BotStatusView.as_view(), name='bot-status'),  # Новый маршрут
]