import { useEffect } from 'react';
import { Platform } from 'react-native';
import Constants from 'expo-constants';
import * as Notifications from 'expo-notifications';

import * as api from '../auth/api';
import { useAuth } from '../auth/AuthContext';
import { getInstallationId, savePushDeviceId } from '../auth/storage';

Notifications.setNotificationHandler({
  handleNotification: async () => ({
    shouldPlaySound: true,
    shouldSetBadge: true,
    shouldShowBanner: true,
    shouldShowList: true,
  }),
});

function idempotencyKey() {
  return `android-push-token-${Date.now()}-${Math.random().toString(36).slice(2, 10)}`;
}

function safeDestination(value: unknown) {
  if (typeof value !== 'string') return '';
  return value.startsWith('/orders/') || value.startsWith('/products') ? value : '';
}

export default function NotificationBridge({
  onDestination,
  onNotificationReceived,
}: {
  onDestination: (destination: string) => void;
  onNotificationReceived: () => void;
}) {
  const { auth, runAuthenticated } = useAuth();

  useEffect(() => {
    if (!auth?.session.active_tenant || Platform.OS !== 'android') return;
    let active = true;

    const responseSubscription = Notifications.addNotificationResponseReceivedListener((response) => {
      const destination = safeDestination(response.notification.request.content.data?.destination);
      if (destination) onDestination(destination);
    });
    const receivedSubscription = Notifications.addNotificationReceivedListener(() => {
      onNotificationReceived();
    });

    void (async () => {
      try {
        await Notifications.setNotificationChannelAsync('orders', {
          name: 'Orders and operations',
          description: 'New orders and important operational alerts',
          importance: Notifications.AndroidImportance.HIGH,
          vibrationPattern: [0, 250, 150, 250],
          lightColor: '#0B5D3B',
          sound: 'default',
        });
        const existing = await Notifications.getPermissionsAsync();
        const permission = existing.status === 'granted'
          ? existing
          : await Notifications.requestPermissionsAsync();
        if (!active || permission.status !== 'granted') return;
        const projectId = Constants.expoConfig?.extra?.eas?.projectId ?? Constants.easConfig?.projectId;
        if (!projectId) return;
        const expoPushToken = (await Notifications.getExpoPushTokenAsync({ projectId })).data;
        if (!active) return;
        const installationId = await getInstallationId();
        const response = await runAuthenticated((accessToken) => api.registerPushToken(
          accessToken,
          {
            installation_id: installationId,
            platform: 'android',
            expo_push_token: expoPushToken,
            app_version: Constants.expoConfig?.version || '1.0.0',
            device_name: 'Android device',
          },
          idempotencyKey(),
        ));
        await savePushDeviceId(response.data.id);

        const lastResponse = await Notifications.getLastNotificationResponseAsync();
        const destination = safeDestination(lastResponse?.notification.request.content.data?.destination);
        if (active && destination) {
          onDestination(destination);
          await Notifications.clearLastNotificationResponseAsync();
        }
      } catch {
        // The inbox remains available when permission, token, or delivery setup is unavailable.
      }
    })();

    return () => {
      active = false;
      responseSubscription.remove();
      receivedSubscription.remove();
    };
  }, [auth?.session.active_tenant?.tenant_id, onDestination, onNotificationReceived, runAuthenticated]);

  return null;
}
