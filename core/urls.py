# core/urls.py
from django.contrib import admin
from django.urls import path, include, re_path
from django.views.static import serve
from django.conf import settings
from django.conf.urls.static import static
from rest_framework_simplejwt.views import TokenObtainPairView, TokenRefreshView
from bots.views import test_error
from django.views.generic import TemplateView

# Инициализация пустого списка urlpatterns
urlpatterns = []

# Определяем маршрут для статических файлов в начале, чтобы он имел приоритет
if settings.DEBUG:
    urlpatterns += [
        re_path(r'^staticfiles/(?P<path>.*)$', serve, {'document_root': settings.STATICFILES_DIRS[0]}),
    ]

urlpatterns += [
    # Административная панель
    path('admin/', admin.site.urls),

    # Маршруты для пользователей
    path('api/users/', include('users.urls')),

    # Маршруты для JWT-аутентификации
    path('api/token/', TokenObtainPairView.as_view(), name='token_obtain_pair'),
    path('api/token/refresh/', TokenRefreshView.as_view(), name='token_refresh'),

    # Маршруты для API-ключей и ботов
    path('api/bots/', include('bots.urls')),

    # Маршруты для социального входа (Google, Telegram через allauth)
    path('accounts/', include('allauth.urls')),

    # Тестовый маршрут для проверки отправки ошибок в Sentry
    path('test-error/', test_error, name='test_error'),

    # Маршрут для django-debug-toolbar (доступен только в DEBUG режиме)
    path('__debug__/', include('debug_toolbar.urls')),

    # Маршрут для дашборда (после авторизации)
    path('dashboard/', TemplateView.as_view(template_name='dashboard.html'), name='dashboard'),
]

# Обслуживание статического index.html для корневого URL в режиме отладки
if settings.DEBUG:
    urlpatterns = [
        re_path(r'^$', serve, {'path': 'index.html', 'document_root': settings.STATICFILES_DIRS[0]}),
    ] + urlpatterns

# Общий маршрут для SPA, исключающий служебные пути
if settings.DEBUG:
    urlpatterns += [
        re_path(r'^(?!api|accounts|admin|test-error|__debug__|dashboard).*$', serve, {'path': 'index.html', 'document_root': settings.STATICFILES_DIRS[0]}),
    ]

# Динамическое обслуживание статических файлов из STATIC_ROOT (для продакшена)
if settings.DEBUG:
    urlpatterns += static(settings.STATIC_URL, document_root=settings.STATIC_ROOT)