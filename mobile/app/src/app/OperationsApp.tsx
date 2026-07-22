import { useCallback, useEffect, useMemo, useState } from 'react';
import MaterialCommunityIcons from '@expo/vector-icons/MaterialCommunityIcons';
import {
  ActivityIndicator,
  Pressable,
  RefreshControl,
  ScrollView,
  StyleSheet,
  Text,
  View,
} from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';

import * as api from '../auth/api';
import { useAuth } from '../auth/AuthContext';
import type { DashboardMetricTone, DashboardResponse } from '../auth/types';
import OrdersScreen from '../orders/OrdersScreen';
import type { OrderListFilters } from '../orders/types';
import StockScreen from '../stock/StockScreen';

type AppTab = 'dashboard' | 'orders' | 'stock' | 'account';
type TabIconName = keyof typeof MaterialCommunityIcons.glyphMap;

const TABS: { key: AppTab; label: string; icon: TabIconName; activeIcon: TabIconName }[] = [
  { key: 'dashboard', label: 'Dashboard', icon: 'view-dashboard-outline', activeIcon: 'view-dashboard' },
  { key: 'orders', label: 'Orders', icon: 'clipboard-text-outline', activeIcon: 'clipboard-text' },
  { key: 'stock', label: 'Stock', icon: 'package-variant-closed', activeIcon: 'package-variant' },
  { key: 'account', label: 'Account', icon: 'account-circle-outline', activeIcon: 'account-circle' },
];

const TONE_STYLES: Record<DashboardMetricTone, { card: object; value: object }> = {
  neutral: { card: { borderColor: '#D9E2DE' }, value: { color: '#17352A' } },
  positive: { card: { borderColor: '#B9DAC8' }, value: { color: '#147348' } },
  attention: { card: { borderColor: '#F0D08D' }, value: { color: '#9A5B00' } },
  critical: { card: { borderColor: '#F1B7B3' }, value: { color: '#B42318' } },
};

function formatUpdatedAt(value?: string) {
  if (!value) return '';
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return '';
  return parsed.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
}

function destinationParameter(destination: string, key: string) {
  const query = destination.split('?', 2)[1] || '';
  const entry = query.split('&').find((part) => part.split('=', 1)[0] === key);
  if (!entry) return undefined;
  return decodeURIComponent(entry.slice(entry.indexOf('=') + 1));
}

function orderFiltersFromDestination(destination: string): OrderListFilters {
  return {
    status: destinationParameter(destination, 'status'),
    date_from: destinationParameter(destination, 'date_from'),
    date_to: destinationParameter(destination, 'date_to'),
  };
}

function DashboardScreen({ onNavigate }: { onNavigate: (destination: string) => void }) {
  const { runAuthenticated } = useAuth();
  const [dashboard, setDashboard] = useState<DashboardResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState('');

  const loadDashboard = useCallback(async (refresh = false) => {
    if (refresh) setRefreshing(true); else setLoading(true);
    setError('');
    try {
      setDashboard(await runAuthenticated(api.dashboard));
    } catch (reason) {
      setError(reason instanceof api.ApiError ? reason.message : 'Dashboard could not be loaded.');
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }, [runAuthenticated]);

  useEffect(() => {
    void loadDashboard();
  }, [loadDashboard]);

  if (loading && !dashboard) {
    return <View style={styles.center}><ActivityIndicator size="large" color="#0B5D3B" /><Text style={styles.loadingText}>Loading operations...</Text></View>;
  }

  if (!dashboard) {
    return (
      <View style={styles.center}>
        <Text style={styles.errorTitle}>Dashboard unavailable</Text>
        <Text style={styles.errorMessage}>{error || 'Check your connection and try again.'}</Text>
        <Pressable onPress={() => void loadDashboard()} style={styles.retryButton}><Text style={styles.retryText}>Try again</Text></Pressable>
      </View>
    );
  }

  return (
    <ScrollView
      contentContainerStyle={styles.scrollContent}
      refreshControl={<RefreshControl refreshing={refreshing} onRefresh={() => void loadDashboard(true)} colors={['#0B5D3B']} tintColor="#0B5D3B" />}
    >
      {error ? <View style={styles.warning}><Text style={styles.warningText}>{error} Showing the last loaded data.</Text></View> : null}
      <View style={styles.sectionHeading}>
        <Text style={styles.sectionTitle}>This month's orders</Text>
        <Text style={styles.updatedText}>Updated {formatUpdatedAt(dashboard.meta.server_time) || 'recently'}</Text>
      </View>
      <View style={styles.metricGrid}>
        {dashboard.data.metrics.map((metric) => {
          const tone = TONE_STYLES[metric.tone] || TONE_STYLES.neutral;
          return (
            <Pressable
              accessibilityRole="button"
              key={metric.key}
              onPress={() => onNavigate(metric.destination)}
              style={({ pressed }) => [styles.metricCard, tone.card, pressed && styles.pressed]}
            >
              <Text style={[styles.metricValue, tone.value]}>{metric.value}</Text>
              <Text style={styles.metricLabel}>{metric.label}</Text>
              <Text style={styles.metricAction}>View details</Text>
            </Pressable>
          );
        })}
      </View>

      <Text style={[styles.sectionTitle, styles.alertHeading]}>Attention</Text>
      {dashboard.data.alerts.length ? dashboard.data.alerts.map((alert) => (
        <Pressable
          key={alert.id}
          onPress={() => onNavigate(alert.destination)}
          style={({ pressed }) => [styles.alertCard, pressed && styles.pressed]}
        >
          <View style={styles.alertDot} />
          <View style={styles.alertCopy}>
            <Text style={styles.alertTitle}>{alert.title}</Text>
            <Text style={styles.alertMessage}>{alert.message}</Text>
          </View>
          <Text style={styles.chevron}>›</Text>
        </Pressable>
      )) : (
        <View style={styles.clearCard}>
          <Text style={styles.clearTitle}>No urgent issues</Text>
          <Text style={styles.clearMessage}>Orders and stock are within the current alert thresholds.</Text>
        </View>
      )}
    </ScrollView>
  );
}

function AccountScreen() {
  const { auth, signOut } = useAuth();
  const [busy, setBusy] = useState(false);
  if (!auth?.session.active_tenant) return null;

  const signOutNow = async () => {
    setBusy(true);
    await signOut();
  };

  return (
    <ScrollView contentContainerStyle={styles.scrollContent}>
      <Text style={styles.sectionTitle}>Account</Text>
      <View style={styles.profileCard}>
        <View style={styles.avatar}><Text style={styles.avatarText}>{auth.session.user.display_name.slice(0, 1).toUpperCase()}</Text></View>
        <View style={styles.profileCopy}>
          <Text style={styles.profileName}>{auth.session.user.display_name}</Text>
          <Text style={styles.profileUsername}>@{auth.session.user.username}</Text>
        </View>
      </View>
      <View style={styles.detailCard}>
        <Text style={styles.detailLabel}>Workspace</Text>
        <Text style={styles.detailValue}>{auth.session.active_tenant.tenant_name}</Text>
        <View style={styles.divider} />
        <Text style={styles.detailLabel}>Role</Text>
        <Text style={styles.detailValue}>{auth.session.active_tenant.role_label}</Text>
      </View>
      <Pressable disabled={busy} onPress={() => void signOutNow()} style={({ pressed }) => [styles.signOutButton, (pressed || busy) && styles.pressed]}>
        {busy ? <ActivityIndicator color="#B42318" /> : <Text style={styles.signOutText}>Sign out</Text>}
      </Pressable>
    </ScrollView>
  );
}

export default function OperationsApp() {
  const { auth } = useAuth();
  const [activeTab, setActiveTab] = useState<AppTab>('dashboard');
  const [ordersInitialFilters, setOrdersInitialFilters] = useState<OrderListFilters>({});
  const [ordersScreenKey, setOrdersScreenKey] = useState(0);

  const openDestination = useCallback((destination: string) => {
    if (destination.startsWith('/products')) {
      setActiveTab('stock');
      return;
    }
    setOrdersInitialFilters(orderFiltersFromDestination(destination));
    setOrdersScreenKey((current) => current + 1);
    setActiveTab('orders');
  }, []);

  const openTab = useCallback((tab: AppTab) => {
    if (tab === 'orders') {
      setOrdersInitialFilters({});
      setOrdersScreenKey((current) => current + 1);
    }
    setActiveTab(tab);
  }, []);
  const title = useMemo(() => {
    if (activeTab === 'dashboard') return `Hello, ${auth?.session.user.display_name || ''}`;
    return TABS.find((tab) => tab.key === activeTab)?.label || '';
  }, [activeTab, auth?.session.user.display_name]);

  return (
    <SafeAreaView style={styles.app}>
      <View style={styles.header}>
        <View>
          <Text style={styles.headerEyebrow}>{auth?.session.active_tenant?.tenant_name}</Text>
          <Text style={styles.headerTitle}>{title}</Text>
        </View>
        <View style={styles.liveBadge}><View style={styles.liveDot} /><Text style={styles.liveText}>LIVE</Text></View>
      </View>

      <View style={styles.content}>
        {activeTab === 'dashboard' ? <DashboardScreen onNavigate={openDestination} /> : null}
        {activeTab === 'orders' ? <OrdersScreen initialFilters={ordersInitialFilters} key={ordersScreenKey} /> : null}
        {activeTab === 'stock' ? <StockScreen /> : null}
        {activeTab === 'account' ? <AccountScreen /> : null}
      </View>

      <View style={styles.tabBar}>
        {TABS.map((tab) => {
          const selected = tab.key === activeTab;
          return (
            <Pressable
              accessibilityRole="tab"
              accessibilityState={{ selected }}
              key={tab.key}
              onPress={() => openTab(tab.key)}
              style={({ pressed }) => [styles.tab, pressed && styles.pressed]}
            >
              <View style={[styles.tabIndicator, selected && styles.tabIndicatorActive]} />
              <MaterialCommunityIcons
                color={selected ? '#0B5D3B' : '#71867D'}
                name={selected ? tab.activeIcon : tab.icon}
                size={25}
                style={styles.tabIcon}
              />
              <Text style={[styles.tabText, selected && styles.tabTextActive]}>{tab.label}</Text>
            </Pressable>
          );
        })}
      </View>
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  app: { flex: 1, backgroundColor: '#F4F7F5' },
  header: { minHeight: 92, backgroundColor: '#0B5D3B', paddingHorizontal: 22, paddingVertical: 15, flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between' },
  headerEyebrow: { color: '#BFE2D2', fontSize: 12, fontWeight: '800', letterSpacing: 0.8, textTransform: 'uppercase' },
  headerTitle: { color: '#FFFFFF', fontSize: 24, fontWeight: '800', marginTop: 5 },
  liveBadge: { backgroundColor: '#174E36', borderRadius: 20, flexDirection: 'row', alignItems: 'center', paddingHorizontal: 10, paddingVertical: 7 },
  liveDot: { width: 7, height: 7, borderRadius: 4, backgroundColor: '#65D49B', marginRight: 6 },
  liveText: { color: '#FFFFFF', fontSize: 10, fontWeight: '900', letterSpacing: 0.8 },
  content: { flex: 1 },
  scrollContent: { padding: 20, paddingBottom: 28 },
  center: { flex: 1, alignItems: 'center', justifyContent: 'center', padding: 30 },
  loadingText: { color: '#587066', marginTop: 14, fontWeight: '600' },
  warning: { backgroundColor: '#FFF4D8', borderColor: '#F0D08D', borderWidth: 1, borderRadius: 12, padding: 12, marginBottom: 16 },
  warningText: { color: '#7A4A00', lineHeight: 19 },
  sectionHeading: { flexDirection: 'row', alignItems: 'flex-end', justifyContent: 'space-between', marginBottom: 14 },
  sectionTitle: { color: '#17352A', fontSize: 20, fontWeight: '800' },
  updatedText: { color: '#71867D', fontSize: 12 },
  metricGrid: { flexDirection: 'row', flexWrap: 'wrap', justifyContent: 'space-between', rowGap: 12 },
  metricCard: { width: '48%', minHeight: 148, backgroundColor: '#FFFFFF', borderRadius: 16, borderWidth: 1, padding: 16 },
  metricValue: { fontSize: 34, fontWeight: '900' },
  metricLabel: { color: '#29483D', fontSize: 14, fontWeight: '700', lineHeight: 19, marginTop: 5, flex: 1 },
  metricAction: { color: '#0B5D3B', fontSize: 12, fontWeight: '800', marginTop: 8 },
  alertHeading: { marginTop: 26, marginBottom: 12 },
  alertCard: { backgroundColor: '#FFFFFF', borderColor: '#E0E7E3', borderWidth: 1, borderRadius: 15, padding: 15, marginBottom: 10, flexDirection: 'row', alignItems: 'center' },
  alertDot: { width: 9, height: 9, borderRadius: 5, backgroundColor: '#D92D20', marginRight: 12 },
  alertCopy: { flex: 1 },
  alertTitle: { color: '#17352A', fontWeight: '800', fontSize: 15 },
  alertMessage: { color: '#587066', lineHeight: 19, marginTop: 3 },
  chevron: { color: '#82958D', fontSize: 28, marginLeft: 8 },
  clearCard: { backgroundColor: '#E4F3EB', borderRadius: 15, padding: 17 },
  clearTitle: { color: '#174E36', fontSize: 16, fontWeight: '800' },
  clearMessage: { color: '#3D6958', lineHeight: 20, marginTop: 5 },
  errorTitle: { color: '#17352A', fontSize: 21, fontWeight: '800', textAlign: 'center' },
  errorMessage: { color: '#587066', lineHeight: 21, textAlign: 'center', marginTop: 8 },
  retryButton: { backgroundColor: '#0B5D3B', minHeight: 48, borderRadius: 13, paddingHorizontal: 24, alignItems: 'center', justifyContent: 'center', marginTop: 20 },
  retryText: { color: '#FFFFFF', fontWeight: '800' },
  profileCard: { backgroundColor: '#FFFFFF', borderRadius: 17, padding: 18, flexDirection: 'row', alignItems: 'center', marginTop: 16 },
  avatar: { width: 54, height: 54, borderRadius: 27, backgroundColor: '#0B5D3B', alignItems: 'center', justifyContent: 'center' },
  avatarText: { color: '#FFFFFF', fontSize: 23, fontWeight: '900' },
  profileCopy: { marginLeft: 14 },
  profileName: { color: '#17352A', fontSize: 18, fontWeight: '800' },
  profileUsername: { color: '#71867D', marginTop: 3 },
  detailCard: { backgroundColor: '#FFFFFF', borderRadius: 17, padding: 18, marginTop: 14 },
  detailLabel: { color: '#71867D', fontSize: 12, fontWeight: '700', textTransform: 'uppercase', letterSpacing: 0.7 },
  detailValue: { color: '#17352A', fontSize: 16, fontWeight: '800', marginTop: 5 },
  divider: { height: 1, backgroundColor: '#E4EAE7', marginVertical: 16 },
  signOutButton: { minHeight: 52, borderColor: '#E5AAA5', borderWidth: 1, borderRadius: 14, alignItems: 'center', justifyContent: 'center', marginTop: 18 },
  signOutText: { color: '#B42318', fontWeight: '800' },
  tabBar: { minHeight: 76, backgroundColor: '#FFFFFF', borderTopColor: '#DCE5E1', borderTopWidth: 1, flexDirection: 'row', paddingHorizontal: 6 },
  tab: { flex: 1, alignItems: 'center', justifyContent: 'center' },
  tabIndicator: { width: 22, height: 3, borderRadius: 2, backgroundColor: 'transparent', marginBottom: 4 },
  tabIndicatorActive: { backgroundColor: '#0B5D3B' },
  tabIcon: { marginBottom: 2 },
  tabText: { color: '#71867D', fontSize: 12, fontWeight: '700' },
  tabTextActive: { color: '#0B5D3B', fontWeight: '900' },
  pressed: { opacity: 0.65 },
});
