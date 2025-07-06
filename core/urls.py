from django.contrib import admin
from django.urls import path, include, re_path
from django.views.static import serve
from django.conf import settings
from django.conf.urls.static import static
from rest_framework_simplejwt.views import TokenObtainPairView, TokenRefreshView
from bots.views import test_error

urlpatterns = [
    re_path(r'^staticfiles/(?P<path>.*)$', serve, {'document_root': settings.STATICFILES_DIRS[0]}),
    path('admin/', admin.site.urls),
    path('api/users/', include('users.urls')),
    path('api/token/', TokenObtainPairView.as_view(), name='token_obtain_pair'),
    path('api/token/refresh/', TokenRefreshView.as_view(), name='token_refresh'),
    path('api/bots/', include('bots.urls')),
    path('accounts/', include('allauth.urls')),  # Обрабатывает все маршруты allauth, включая Telegram и Google
    path('test-error/', test_error, name='test_error'),
    path('__debug__/', include('debug_toolbar.urls')),
]

# Обслуживание index.html для всех SPA-маршрутов (исключая accounts/)
if settings.DEBUG:
    urlpatterns += [
        re_path(r'^$', serve, {'path': 'index.html', 'document_root': settings.STATICFILES_DIRS[0]}),
        re_path(r'^(?!staticfiles|admin|api|accounts/|test-error|__debug__).*$', serve, {'path': 'index.html', 'document_root': settings.STATICFILES_DIRS[0]}),
    ]

# Динамическое обслуживание статических файлов
if settings.DEBUG:
    urlpatterns += static(settings.STATIC_URL, document_root=settings.STATIC_ROOT)