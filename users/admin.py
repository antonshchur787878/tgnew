from django.contrib import admin
from django.contrib.auth.admin import UserAdmin
from .models import CustomUser

class CustomUserAdmin(UserAdmin):
    model = CustomUser
    fieldsets = UserAdmin.fieldsets + (
        (None, {'fields': ('referral_code', 'balance', 'telegram_id')}),
    )
    list_display = ['username', 'email', 'referral_code', 'balance', 'telegram_id']

admin.site.register(CustomUser, CustomUserAdmin)