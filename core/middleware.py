import logging

logger = logging.getLogger(__name__)

class OAuthRedirectLoggerMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        if request.path.startswith('/accounts/google/login/') and hasattr(request, 'social_strategy'):
            logger.info(f"Google login response with potential redirect URI: {response.get('Location', 'No redirect')}")
            if request.user.is_authenticated:
                logger.info(f"User {request.user.username} is authenticated, redirecting to /dashboard/")
            else:
                logger.info("User is not authenticated")
        return response

    def process_view(self, request, view_func, view_args, view_kwargs):
        if request.path.startswith('/accounts/google/login/'):
            logger.info(f"Google login request with full URI: {request.build_absolute_uri()}")
            if request.user.is_authenticated:
                logger.info(f"User {request.user.username} is authenticated at view processing")
            else:
                logger.info("User is not authenticated at view processing")
        return None