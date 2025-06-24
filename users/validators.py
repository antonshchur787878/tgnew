import re
from django.core.exceptions import ValidationError
from django.utils.translation import gettext as _

class ComplexPasswordValidator:
    """
    Валидатор для проверки сложности пароля.
    Требует минимум 8 символов, включая буквы, цифры и специальные символы.
    """
    def validate(self, password, user=None):
        if len(password) < 8:
            raise ValidationError(
                _("Пароль должен содержать минимум 8 символов."),
                code='password_too_short',
            )
        if not re.search(r'[A-Za-z]', password):
            raise ValidationError(
                _("Пароль должен содержать хотя бы одну букву."),
                code='password_no_letter',
            )
        if not re.search(r'[0-9]', password):
            raise ValidationError(
                _("Пароль должен содержать хотя бы одну цифру."),
                code='password_no_digit',
            )
        if not re.search(r'[!@#$%^&*(),.?":{}|<>]', password):
            raise ValidationError(
                _("Пароль должен содержать хотя бы один специальный символ."),
                code='password_no_special',
            )

    def get_help_text(self):
        return _(
            "Ваш пароль должен содержать минимум 8 символов, включая буквы, цифры и специальные символы."
        )