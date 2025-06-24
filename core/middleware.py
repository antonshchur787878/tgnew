# core/middleware.py
import logging

logger = logging.getLogger(__name__)

class OAuthRedirectLoggerMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        if request.path.startswith('/accounts/google/login/') and hasattr(request, 'social_strategy'):
            logger.info(f"Google login response with potential redirect URI: {response.get('Location', 'No redirect')}")
        return response

    def process_view(self, request, view_func, view_args, view_kwargs):
        if request.path.startswith('/accounts/google/login/'):
            logger.info(f"Google login request with full URI: {request.build_absolute_uri()}")
        return None