import { useCallback, useEffect, useState } from 'react';
import MaterialCommunityIcons from '@expo/vector-icons/MaterialCommunityIcons';
import {
  ActivityIndicator,
  FlatList,
  Pressable,
  RefreshControl,
  StyleSheet,
  Switch,
  Text,
  View,
} from 'react-native';

import * as api from '../auth/api';
import { useAuth } from '../auth/AuthContext';
import type { MobileNotification, NotificationCategory, NotificationPreference } from './types';

const CATEGORY_LABELS: Record<NotificationCategory, string> = {
  new_order: 'New orders',
  order_attention: 'Order attention',
  status_change: 'Status changes',
  routing_alert: 'Routing alerts',
  integration_alert: 'Integration alerts',
};

const CATEGORY_ICONS: Record<NotificationCategory, keyof typeof MaterialCommunityIcons.glyphMap> = {
  new_order: 'clipboard-plus-outline',
  order_attention: 'alert-circle-outline',
  status_change: 'swap-horizontal-circle-outline',
  routing_alert: 'map-marker-alert-outline',
  integration_alert: 'connection',
};

function idempotencyKey() {
  return `android-notification-${Date.now()}-${Math.random().toString(36).slice(2, 10)}`;
}

function notificationTime(value: string) {
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return '';
  return parsed.toLocaleString([], { day: '2-digit', month: 'short', hour: '2-digit', minute: '2-digit' });
}

export default function NotificationsScreen({
  onOpenDestination,
  onUnreadCountChange,
}: {
  onOpenDestination: (destination: string) => void;
  onUnreadCountChange: (count: number) => void;
}) {
  const { runAuthenticated } = useAuth();
  const [notifications, setNotifications] = useState<MobileNotification[]>([]);
  const [preferences, setPreferences] = useState<NotificationPreference[]>([]);
  const [nextCursor, setNextCursor] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [loadingMore, setLoadingMore] = useState(false);
  const [savingCategory, setSavingCategory] = useState<NotificationCategory | null>(null);
  const [showPreferences, setShowPreferences] = useState(false);
  const [error, setError] = useState('');

  const load = useCallback(async (refresh = false) => {
    if (refresh) setRefreshing(true); else setLoading(true);
    setError('');
    try {
      const [inbox, preferenceResponse] = await Promise.all([
        runAuthenticated((token) => api.notifications(token)),
        runAuthenticated(api.notificationPreferences),
      ]);
      setNotifications(inbox.data);
      setNextCursor(inbox.pagination.next_cursor);
      setPreferences(preferenceResponse.data);
      onUnreadCountChange(inbox.meta.unread_count || 0);
    } catch (reason) {
      setError(reason instanceof api.ApiError ? reason.message : 'Notifications could not be loaded.');
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }, [onUnreadCountChange, runAuthenticated]);

  useEffect(() => { void load(); }, [load]);

  const loadMore = async () => {
    if (!nextCursor || loadingMore) return;
    setLoadingMore(true);
    try {
      const response = await runAuthenticated((token) => api.notifications(token, { cursor: nextCursor }));
      setNotifications((current) => [...current, ...response.data]);
      setNextCursor(response.pagination.next_cursor);
      onUnreadCountChange(response.meta.unread_count || 0);
    } catch (reason) {
      setError(reason instanceof api.ApiError ? reason.message : 'More notifications could not be loaded.');
    } finally {
      setLoadingMore(false);
    }
  };

  const openNotification = async (notification: MobileNotification) => {
    let updated = notification;
    if (!notification.is_read) {
      try {
        const response = await runAuthenticated((token) => api.markNotificationRead(token, notification.id, idempotencyKey()));
        updated = response.data;
        setNotifications((current) => current.map((row) => row.id === updated.id ? updated : row));
        onUnreadCountChange(Math.max(0, notifications.filter((row) => !row.is_read).length - 1));
      } catch (reason) {
        setError(reason instanceof api.ApiError ? reason.message : 'Notification could not be marked read.');
      }
    }
    const destination = updated.destination || (updated.order_id ? `/orders/${updated.order_id}` : '');
    if (destination) onOpenDestination(destination);
  };

  const togglePreference = async (preference: NotificationPreference) => {
    if (preference.mandatory || savingCategory) return;
    const nextEnabled = !preference.enabled;
    setSavingCategory(preference.category);
    try {
      const response = await runAuthenticated((token) => api.updateNotificationPreferences(
        token,
        [{ category: preference.category, enabled: nextEnabled }],
        idempotencyKey(),
      ));
      setPreferences(response.data);
    } catch (reason) {
      setError(reason instanceof api.ApiError ? reason.message : 'Preference could not be updated.');
    } finally {
      setSavingCategory(null);
    }
  };

  const header = (
    <View>
      <View style={styles.headingRow}>
        <View>
          <Text style={styles.heading}>Notification inbox</Text>
          <Text style={styles.headingHint}>New orders and operational alerts</Text>
        </View>
        <Pressable onPress={() => setShowPreferences((current) => !current)} style={styles.settingsButton}>
          <MaterialCommunityIcons color="#0B5D3B" name="tune-variant" size={21} />
          <Text style={styles.settingsText}>Settings</Text>
        </Pressable>
      </View>
      {showPreferences ? (
        <View style={styles.preferencesCard}>
          {preferences.map((preference, index) => (
            <View key={preference.category} style={[styles.preferenceRow, index > 0 && styles.preferenceDivider]}>
              <View style={styles.preferenceCopy}>
                <Text style={styles.preferenceTitle}>{CATEGORY_LABELS[preference.category]}</Text>
                <Text style={styles.preferenceHint}>{preference.mandatory ? 'Required operational alert' : 'You can turn this alert off'}</Text>
              </View>
              <Switch
                disabled={preference.mandatory || savingCategory === preference.category}
                onValueChange={() => void togglePreference(preference)}
                thumbColor="#FFFFFF"
                trackColor={{ false: '#CBD9D3', true: '#0B5D3B' }}
                value={preference.enabled}
              />
            </View>
          ))}
        </View>
      ) : null}
      {error ? <View style={styles.warning}><Text style={styles.warningText}>{error}</Text></View> : null}
    </View>
  );

  if (loading && !notifications.length) {
    return <View style={styles.center}><ActivityIndicator size="large" color="#0B5D3B" /><Text style={styles.loadingText}>Loading notifications...</Text></View>;
  }

  return (
    <FlatList
      contentContainerStyle={styles.content}
      data={notifications}
      keyExtractor={(item) => String(item.id)}
      ListEmptyComponent={<View style={styles.empty}><MaterialCommunityIcons color="#82958D" name="bell-check-outline" size={42} /><Text style={styles.emptyTitle}>You are all caught up</Text><Text style={styles.emptyText}>New order alerts will appear here.</Text></View>}
      ListFooterComponent={nextCursor ? (
        <Pressable disabled={loadingMore} onPress={() => void loadMore()} style={styles.loadMore}>
          {loadingMore ? <ActivityIndicator color="#0B5D3B" /> : <Text style={styles.loadMoreText}>Load more</Text>}
        </Pressable>
      ) : null}
      ListHeaderComponent={header}
      refreshControl={<RefreshControl refreshing={refreshing} onRefresh={() => void load(true)} colors={['#0B5D3B']} tintColor="#0B5D3B" />}
      renderItem={({ item }) => (
        <Pressable onPress={() => void openNotification(item)} style={({ pressed }) => [styles.notificationCard, !item.is_read && styles.unreadCard, pressed && styles.pressed]}>
          <View style={[styles.iconWrap, !item.is_read && styles.unreadIconWrap]}>
            <MaterialCommunityIcons color={item.is_read ? '#587066' : '#0B5D3B'} name={CATEGORY_ICONS[item.category]} size={22} />
          </View>
          <View style={styles.notificationCopy}>
            <View style={styles.titleRow}>
              <Text numberOfLines={1} style={styles.notificationTitle}>{item.title}</Text>
              {!item.is_read ? <View style={styles.unreadDot} /> : null}
            </View>
            <Text style={styles.notificationMessage}>{item.message}</Text>
            <Text style={styles.notificationTime}>{notificationTime(item.created_at)}</Text>
          </View>
          {item.destination || item.order_id ? <MaterialCommunityIcons color="#82958D" name="chevron-right" size={24} /> : null}
        </Pressable>
      )}
    />
  );
}

const styles = StyleSheet.create({
  center: { flex: 1, alignItems: 'center', justifyContent: 'center', padding: 30 },
  loadingText: { color: '#587066', marginTop: 12, fontWeight: '600' },
  content: { padding: 18, paddingBottom: 28 },
  headingRow: { flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', marginBottom: 16 },
  heading: { color: '#17352A', fontSize: 20, fontWeight: '900' },
  headingHint: { color: '#71867D', fontSize: 12, marginTop: 4 },
  settingsButton: { minHeight: 42, borderColor: '#B8D5C8', borderWidth: 1, borderRadius: 12, paddingHorizontal: 11, flexDirection: 'row', alignItems: 'center', columnGap: 6 },
  settingsText: { color: '#0B5D3B', fontSize: 12, fontWeight: '800' },
  preferencesCard: { backgroundColor: '#FFFFFF', borderColor: '#DEE7E3', borderWidth: 1, borderRadius: 15, paddingHorizontal: 15, marginBottom: 16 },
  preferenceRow: { minHeight: 70, flexDirection: 'row', alignItems: 'center' },
  preferenceDivider: { borderTopColor: '#E7ECEA', borderTopWidth: 1 },
  preferenceCopy: { flex: 1, paddingRight: 12 },
  preferenceTitle: { color: '#29483D', fontSize: 14, fontWeight: '800' },
  preferenceHint: { color: '#71867D', fontSize: 11, marginTop: 3 },
  warning: { backgroundColor: '#FFF4D8', borderRadius: 11, padding: 11, marginBottom: 14 },
  warningText: { color: '#7A4A00', lineHeight: 18 },
  notificationCard: { backgroundColor: '#FFFFFF', borderColor: '#DEE7E3', borderWidth: 1, borderRadius: 15, padding: 14, marginBottom: 10, flexDirection: 'row', alignItems: 'center' },
  unreadCard: { backgroundColor: '#F3FAF6', borderColor: '#B8D5C8' },
  iconWrap: { width: 44, height: 44, borderRadius: 22, backgroundColor: '#EEF3F1', alignItems: 'center', justifyContent: 'center', marginRight: 12 },
  unreadIconWrap: { backgroundColor: '#DDF2E7' },
  notificationCopy: { flex: 1 },
  titleRow: { flexDirection: 'row', alignItems: 'center' },
  notificationTitle: { flex: 1, color: '#17352A', fontSize: 15, fontWeight: '900' },
  unreadDot: { width: 8, height: 8, borderRadius: 4, backgroundColor: '#0B5D3B', marginLeft: 8 },
  notificationMessage: { color: '#587066', fontSize: 13, lineHeight: 19, marginTop: 4 },
  notificationTime: { color: '#82958D', fontSize: 11, marginTop: 7 },
  empty: { alignItems: 'center', paddingVertical: 70 },
  emptyTitle: { color: '#29483D', fontSize: 18, fontWeight: '800', marginTop: 12 },
  emptyText: { color: '#71867D', marginTop: 5 },
  loadMore: { minHeight: 48, alignItems: 'center', justifyContent: 'center' },
  loadMoreText: { color: '#0B5D3B', fontWeight: '800' },
  pressed: { opacity: 0.65 },
});
