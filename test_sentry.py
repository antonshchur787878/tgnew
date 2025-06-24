import sentry_sdk

def test_sentry_integration():
    try:
        # Искусственная ошибка для проверки отправки в Sentry
        division_by_zero = 1 / 0
    except Exception as e:
        # Отправка исключения в Sentry
        sentry_sdk.capture_exception(e)
        print("Ошибка отправлена в Sentry")

# Запуск тестовой функции
test_sentry_integration()