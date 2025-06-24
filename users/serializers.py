from rest_framework import serializers
from .models import CustomUser


class UserSerializer(serializers.ModelSerializer):
    password = serializers.CharField(write_only=True)  # Пароль не возвращается в ответе

    class Meta:
        model = CustomUser
        fields = ['id', 'username', 'email', 'password', 'referral_code', 'balance']  # Учитываем добавленные поля

    def create(self, validated_data):
        # Используем метод create_user для хеширования пароля
        user = CustomUser.objects.create_user(
            username=validated_data['username'],
            email=validated_data['email'],
            password=validated_data['password'],
            referral_code=validated_data.get('referral_code'),
            balance=validated_data.get('balance', 0.00)
        )
        return user