# Generated by Django 5.2 on 2025-04-16 17:26

from django.conf import settings
from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('bots', '0001_initial'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AlterUniqueTogether(
            name='apikey',
            unique_together={('user', 'api_key')},
        ),
    ]
