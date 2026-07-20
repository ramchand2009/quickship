"""Serializers for the version 1 mobile API."""

from rest_framework import serializers


class LoginRequestSerializer(serializers.Serializer):
    username = serializers.CharField(max_length=150, trim_whitespace=True)
    password = serializers.CharField(max_length=1024, trim_whitespace=False, write_only=True)
    installation_id = serializers.UUIDField()
    platform = serializers.ChoiceField(choices=["android"])
    app_version = serializers.CharField(max_length=32, allow_blank=True)


class RefreshRequestSerializer(serializers.Serializer):
    refresh_token = serializers.CharField(min_length=32, max_length=512, write_only=True)
    installation_id = serializers.UUIDField()


class LogoutRequestSerializer(RefreshRequestSerializer):
    pass
