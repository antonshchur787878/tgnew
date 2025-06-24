from django.contrib import admin
from .models import APIKey, Bot, LogEntry, BotSettings, BotPerformanceSummary


@admin.register(APIKey)
class APIKeyAdmin(admin.ModelAdmin):
    """
    Админка для управления API-ключами.
    """
    list_display = ('user', 'exchange', 'added_at')  # Поля для отображения в списке
    list_filter = ('exchange',)  # Фильтры по бирже
    search_fields = ('user__username', 'exchange')  # Поля для поиска


@admin.register(Bot)
class BotAdmin(admin.ModelAdmin):
    """
    Админка для управления ботами.
    """
    list_display = ('name', 'user', 'strategy', 'trading_pair', 'deposit', 'status', 'created_at')  # Поля для отображения
    list_filter = ('strategy', 'status')  # Фильтры по стратегии и статусу
    search_fields = ('name', 'trading_pair')  # Поля для поиска
    readonly_fields = ('created_at',)  # Поля только для чтения


@admin.register(LogEntry)
class LogEntryAdmin(admin.ModelAdmin):
    """
    Админка для управления логами ботов.
    """
    list_display = ('timestamp', 'bot', 'action', 'status', 'financial_result_display')  # Добавлено отображение financial_result
    list_filter = ('status', 'bot')  # Фильтры по статусу и боту
    search_fields = ('details', 'error_message')  # Поля для поиска
    readonly_fields = ('timestamp',)  # Поля только для чтения

    def financial_result_display(self, obj):
        return obj.financial_result if obj.financial_result else "-"
    financial_result_display.short_description = "Финансовый результат"


@admin.register(BotSettings)
class BotSettingsAdmin(admin.ModelAdmin):
    """
    Админка для управления настройками ботов.
    """
    list_display = ('bot', 'signal_type', 'signal_interval', 'take_profit')
    list_filter = ('signal_type',)
    search_fields = ('bot__name',)


@admin.register(BotPerformanceSummary)
class BotPerformanceSummaryAdmin(admin.ModelAdmin):
    """
    Админка для управления сводками производительности ботов.
    """
    list_display = ('bot', 'period_start', 'period_end', 'total_profit', 'roi')
    list_filter = ('period_start',)
    search_fields = ('bot__name',)