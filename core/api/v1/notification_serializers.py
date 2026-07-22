from rest_framework import serializers

from core.models import MobileDevice, MobileNotification


class NotificationListQuerySerializer(serializers.Serializer):
    unread_only = serializers.BooleanField(required=False, default=False)


class MobileNotificationSerializer(serializers.ModelSerializer):
    destination = serializers.SerializerMethodField()
    order_id = serializers.IntegerField(allow_null=True)

    class Meta:
        model = MobileNotification
        fields = [
            "id",
            "category",
            "title",
            "message",
            "destination",
            "order_id",
            "is_read",
            "read_at",
            "created_at",
        ]

    def get_destination(self, notification):
        return notification.destination or None


class NotificationPreferenceUpdateSerializer(serializers.Serializer):
    category = serializers.ChoiceField(choices=MobileNotification.CATEGORY_CHOICES)
    enabled = serializers.BooleanField()


class NotificationPreferencesUpdateSerializer(serializers.Serializer):
    preferences = NotificationPreferenceUpdateSerializer(many=True, min_length=1, max_length=5)

    def validate_preferences(self, preferences):
        categories = [row["category"] for row in preferences]
        if len(categories) != len(set(categories)):
            raise serializers.ValidationError("Each category may appear only once.")
        return preferences


class PushTokenRegistrationSerializer(serializers.Serializer):
    installation_id = serializers.UUIDField()
    platform = serializers.ChoiceField(choices=MobileDevice.PLATFORM_CHOICES)
    expo_push_token = serializers.CharField(min_length=16, max_length=512, trim_whitespace=True)
    app_version = serializers.CharField(max_length=32, trim_whitespace=True)
    device_name = serializers.CharField(required=False, allow_blank=True, max_length=120, trim_whitespace=True)


class MobileDeviceSerializer(serializers.ModelSerializer):
    class Meta:
        model = MobileDevice
        fields = [
            "id",
            "installation_id",
            "platform",
            "app_version",
            "enabled",
            "last_seen_at",
        ]
