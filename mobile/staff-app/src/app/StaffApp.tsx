import { useCallback, useEffect, useMemo, useState } from 'react';
import MaterialCommunityIcons from '@expo/vector-icons/MaterialCommunityIcons';
import {
  ActivityIndicator,
  AppState,
  Image,
  Modal,
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
import type { DashboardAlert, DashboardMetric, DashboardResponse } from '../auth/types';
import OrdersScreen from '../orders/OrdersScreen';
import type { OrderListFilters } from '../orders/types';
import StockScreen from '../stock/StockScreen';

type StaffTab = 'home' | 'orders' | 'stock' | 'account';

const TABS: Array<{ key: StaffTab; label: string; icon: keyof typeof MaterialCommunityIcons.glyphMap }> = [
  { key: 'home', label: 'Home', icon: 'home-outline' },
  { key: 'orders', label: 'Orders', icon: 'clipboard-list-outline' },
  { key: 'stock', label: 'Stock', icon: 'cube-outline' },
  { key: 'account', label: 'Account', icon: 'account-circle-outline' },
];

const PIPELINE_KEYS = [
  'total_orders',
  'pending_orders',
  'accepted_orders',
  'shipped_orders',
  'completed_orders',
  'cancelled_orders',
];

const PIPELINE_LABELS: Record<string, string> = {
  total_orders: 'Total orders',
  pending_orders: 'New',
  accepted_orders: 'Accepted',
  shipped_orders: 'Shipped',
  completed_orders: 'Completed',
  cancelled_orders: 'Cancelled',
};

const PIPELINE_ICONS: Record<string, keyof typeof MaterialCommunityIcons.glyphMap> = {
  total_orders: 'clipboard-list-outline',
  pending_orders: 'clock-outline',
  accepted_orders: 'clipboard-check-outline',
  shipped_orders: 'truck-delivery-outline',
  completed_orders: 'package-variant-closed-check',
  cancelled_orders: 'close-circle-outline',
};

const PIPELINE_COLORS: Record<string, { foreground: string; background: string; border: string }> = {
  total_orders: { foreground: '#14733D', background: '#ECF7EE', border: '#B9DDBF' },
  pending_orders: { foreground: '#E68200', background: '#FFF7E8', border: '#F3D28B' },
  accepted_orders: { foreground: '#14733D', background: '#ECF7EE', border: '#B9DDBF' },
  shipped_orders: { foreground: '#1769C2', background: '#EFF6FF', border: '#B6D7FF' },
  completed_orders: { foreground: '#14733D', background: '#ECF7EE', border: '#B9DDBF' },
  cancelled_orders: { foreground: '#D92D3A', background: '#FFF1F2', border: '#FFC2C7' },
};

const PIPELINE_STATUSES: Record<string, string> = {
  pending_orders: 'new_order',
  accepted_orders: 'order_accepted',
  shipped_orders: 'shipped',
  completed_orders: 'completed',
  cancelled_orders: 'order_cancelled',
};

function tabTitle(tab: StaffTab) {
  if (tab === 'home') return 'Home';
  if (tab === 'orders') return 'Staff Orders';
  if (tab === 'stock') return 'Stock Lookup';
  return 'Account';
}

function monthKey(date: Date) {
  return `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, '0')}`;
}

function monthOptions(count = 12) {
  const current = new Date();
  return Array.from({ length: count }, (_, index) => {
    const date = new Date(current.getFullYear(), current.getMonth() - index, 1);
    return { key: monthKey(date), label: date.toLocaleDateString([], { month: 'long', year: 'numeric' }) };
  });
}

function updatedTime(value?: string) {
  if (!value) return 'recently';
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return 'recently';
  return parsed.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
}

function metricValue(metric?: DashboardMetric) {
  if (!metric) return '0';
  return String(metric.value);
}

function statusFromDestination(destination: string) {
  const query = destination.split('?', 2)[1] || '';
  const statusPart = query.split('&').find((part) => part.startsWith('status='));
  if (!statusPart) return '';
  return decodeURIComponent(statusPart.slice('status='.length));
}

function StaffHomeScreen({
  onOpenOrders,
  onOpenStock,
}: {
  onOpenOrders: (filters: OrderListFilters) => void;
  onOpenStock: () => void;
}) {
  const { auth, runAuthenticated } = useAuth();
  const options = useMemo(() => monthOptions(), []);
  const [selectedMonth, setSelectedMonth] = useState(() => monthKey(new Date()));
  const [monthPickerVisible, setMonthPickerVisible] = useState(false);
  const [dashboard, setDashboard] = useState<DashboardResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState('');

  const loadDashboard = useCallback(async (refresh = false, silent = false) => {
    if (!silent) {
      if (refresh) setRefreshing(true); else setLoading(true);
      setError('');
    }
    try {
      setDashboard(await runAuthenticated((accessToken) => api.dashboard(accessToken, selectedMonth)));
    } catch (reason) {
      if (!silent) setError(reason instanceof api.ApiError ? reason.message : 'Home could not be loaded.');
    } finally {
      if (!silent) {
        setLoading(false);
        setRefreshing(false);
      }
    }
  }, [runAuthenticated, selectedMonth]);

  useEffect(() => {
    void loadDashboard();
  }, [loadDashboard]);

  useEffect(() => {
    const refresh = () => void loadDashboard(false, true);
    const interval = setInterval(refresh, 30000);
    const subscription = AppState.addEventListener('change', (state) => {
      if (state === 'active') refresh();
    });
    return () => {
      clearInterval(interval);
      subscription.remove();
    };
  }, [loadDashboard]);

  const selectedLabel = options.find((option) => option.key === selectedMonth)?.label || selectedMonth;
  const tenantName = auth?.session.active_tenant?.tenant_name || 'Mathukai Organic';
  const staffName = auth?.session.user.display_name || auth?.session.user.username || 'Staff';

  if (loading && !dashboard) {
    return (
      <View style={styles.center}>
        <ActivityIndicator size="large" color="#0B5D3B" />
        <Text style={styles.loadingText}>Loading staff home...</Text>
      </View>
    );
  }

  if (!dashboard) {
    return (
      <View style={styles.center}>
        <Text style={styles.errorTitle}>Home unavailable</Text>
        <Text style={styles.errorMessage}>{error || 'Check your connection and try again.'}</Text>
        <Pressable onPress={() => void loadDashboard()} style={styles.retryButton}>
          <Text style={styles.retryText}>Try again</Text>
        </Pressable>
      </View>
    );
  }

  const metricsByKey = dashboard.data.metrics.reduce<Record<string, DashboardMetric>>((acc, metric) => {
    acc[metric.key] = metric;
    return acc;
  }, {});
  const pipelineMetrics = PIPELINE_KEYS.map((key) => metricsByKey[key]).filter(Boolean);
  const alerts = dashboard.data.alerts;

  const openMetric = (metric: DashboardMetric) => {
    const status = PIPELINE_STATUSES[metric.key] || statusFromDestination(metric.destination);
    onOpenOrders({
      status,
      date_from: dashboard.meta.period?.date_from,
      date_to: dashboard.meta.period?.date_to,
    });
  };

  const openAlert = (alert: DashboardAlert) => {
    if (alert.destination.includes('stock')) {
      onOpenStock();
      return;
    }
    onOpenOrders({
      status: statusFromDestination(alert.destination),
      date_from: dashboard.meta.period?.date_from,
      date_to: dashboard.meta.period?.date_to,
    });
  };

  return (
    <>
      <ScrollView
        contentContainerStyle={styles.homeScroll}
        refreshControl={<RefreshControl refreshing={refreshing} onRefresh={() => void loadDashboard(true)} colors={['#0B5D3B']} tintColor="#0B5D3B" />}
      >
        {error ? <View style={styles.warning}><Text style={styles.warningText}>{error} Showing last loaded data.</Text></View> : null}

        <View style={styles.storeCard}>
          <View style={styles.storeIcon}>
            <MaterialCommunityIcons name="storefront-outline" color="#0B5D3B" size={26} />
          </View>
          <View style={styles.storeCopy}>
            <Text style={styles.storeLabel}>Store</Text>
            <Text style={styles.storeName}>{tenantName}</Text>
            <View style={styles.staffNameRow}>
              <MaterialCommunityIcons name="account-badge-outline" color="#0B5D3B" size={16} />
              <Text style={styles.staffName}>Signed in as {staffName}</Text>
            </View>
            <Text style={styles.updatedText}>Updated {updatedTime(dashboard.meta.server_time)}</Text>
          </View>
        </View>

        <View style={styles.monthCard}>
          <View>
            <Text style={styles.monthLabel}>Selected month</Text>
            <Text style={styles.monthValue}>{selectedLabel}</Text>
          </View>
          <Pressable onPress={() => setMonthPickerVisible(true)} style={({ pressed }) => [styles.monthButton, pressed && styles.pressed]}>
            <Text style={styles.monthButtonText}>Change</Text>
            <MaterialCommunityIcons name="chevron-down" color="#0B5D3B" size={18} />
          </Pressable>
        </View>

        <Text style={styles.sectionTitle}>Order count</Text>
        <Pressable onPress={() => metricsByKey.total_orders ? openMetric(metricsByKey.total_orders) : onOpenOrders({})} style={({ pressed }) => [styles.totalOrdersCard, pressed && styles.pressed]}>
          <View style={styles.totalOrdersIcon}>
            <MaterialCommunityIcons name="clipboard-list-outline" color="#0B5D3B" size={28} />
          </View>
          <View>
            <Text style={styles.totalOrdersLabel}>Total orders this month</Text>
            <Text style={styles.totalOrdersValue}>{metricValue(metricsByKey.total_orders)}</Text>
          </View>
          <MaterialCommunityIcons name="chevron-right" color="#52665E" size={26} />
        </Pressable>

        <Text style={[styles.sectionTitle, styles.pipelineHeading]}>Order pipeline</Text>
        <View style={styles.metricGrid}>
          {pipelineMetrics.filter((metric) => metric.key !== 'total_orders').map((metric) => {
            const colors = PIPELINE_COLORS[metric.key] || PIPELINE_COLORS.total_orders;
            return (
              <Pressable key={metric.key} onPress={() => openMetric(metric)} style={({ pressed }) => [styles.metricCard, pressed && styles.pressed]}>
                <View style={[styles.metricIcon, { backgroundColor: colors.background, borderColor: colors.border }]}>
                  <MaterialCommunityIcons name={PIPELINE_ICONS[metric.key] || 'clipboard-list-outline'} color={colors.foreground} size={24} />
                </View>
                <View style={styles.metricCopy}>
                  <Text style={styles.metricLabel}>{PIPELINE_LABELS[metric.key] || metric.label}</Text>
                  <Text style={[styles.metricValue, { color: colors.foreground }]}>{metricValue(metric)}</Text>
                </View>
                <MaterialCommunityIcons name="chevron-right" color="#52665E" size={22} />
              </Pressable>
            );
          })}
        </View>

        <Text style={styles.sectionTitle}>Needs your attention</Text>
        {alerts.length ? alerts.map((alert) => (
          <Pressable key={alert.id} onPress={() => openAlert(alert)} style={({ pressed }) => [styles.attentionCard, pressed && styles.pressed]}>
            <View style={styles.attentionIcon}>
              <MaterialCommunityIcons name="alert-outline" color="#B77900" size={24} />
            </View>
            <View style={styles.attentionCopy}>
              <Text style={styles.attentionTitle}>{alert.title}</Text>
              <Text style={styles.attentionText}>{alert.message}</Text>
            </View>
            <MaterialCommunityIcons name="chevron-right" color="#7B5A12" size={24} />
          </Pressable>
        )) : (
          <View style={styles.noAttentionCard}>
            <MaterialCommunityIcons name="check-circle-outline" color="#0B5D3B" size={24} />
            <Text style={styles.noAttentionText}>No urgent items right now.</Text>
          </View>
        )}
      </ScrollView>

      <Modal transparent visible={monthPickerVisible} animationType="fade" onRequestClose={() => setMonthPickerVisible(false)}>
        <View style={styles.modalBackdrop}>
          <Pressable style={styles.modalDismiss} onPress={() => setMonthPickerVisible(false)} />
          <View style={styles.monthPickerCard}>
            <Text style={styles.monthPickerTitle}>Select month</Text>
            {options.map((option) => (
              <Pressable
                key={option.key}
                onPress={() => {
                  setSelectedMonth(option.key);
                  setMonthPickerVisible(false);
                }}
                style={[styles.monthOption, option.key === selectedMonth && styles.monthOptionSelected]}
              >
                <Text style={[styles.monthOptionText, option.key === selectedMonth && styles.monthOptionTextSelected]}>{option.label}</Text>
                {option.key === selectedMonth ? <MaterialCommunityIcons name="check" color="#0B5D3B" size={20} /> : null}
              </Pressable>
            ))}
          </View>
        </View>
      </Modal>
    </>
  );
}

function AccountScreen() {
  const { auth, signOut } = useAuth();
  const user = auth?.session.user;
  const tenant = auth?.session.active_tenant;

  return (
    <View style={styles.accountPage}>
      <View style={styles.accountCard}>
        <View style={styles.avatar}>
          <Text style={styles.avatarText}>{(user?.display_name || user?.username || 'S').slice(0, 1).toUpperCase()}</Text>
        </View>
        <Text style={styles.accountName}>{user?.display_name || user?.username || 'Staff user'}</Text>
        <Text style={styles.accountMeta}>{tenant?.tenant_name || 'Mathukai Organic'}</Text>
        <Text style={styles.accountRole}>{tenant?.role_label || 'Staff access'}</Text>
      </View>

      <View style={styles.infoCard}>
        <Text style={styles.infoTitle}>Staff app access</Text>
        <Text style={styles.infoText}>This app is focused on daily order processing and stock checks. Owner-only reports, expenses, and profit dashboards are hidden here.</Text>
      </View>

      <Pressable onPress={() => void signOut()} style={({ pressed }) => [styles.signOutButton, pressed && styles.pressed]}>
        <MaterialCommunityIcons name="logout" color="#B42318" size={20} />
        <Text style={styles.signOutText}>Sign out</Text>
      </Pressable>
    </View>
  );
}

export default function StaffApp() {
  const [activeTab, setActiveTab] = useState<StaffTab>('home');
  const [orderFilters, setOrderFilters] = useState<OrderListFilters>({ status: 'new_order' });
  const [ordersKey, setOrdersKey] = useState(0);

  const openOrders = useCallback((filters: OrderListFilters = {}) => {
    setOrderFilters(filters);
    setOrdersKey((current) => current + 1);
    setActiveTab('orders');
  }, []);

  return (
    <SafeAreaView style={styles.shell}>
      <View style={styles.header}>
        <View style={styles.brandRow}>
          <Image
            accessibilityLabel="Mathukai Organic logo"
            resizeMode="contain"
            source={require('../../assets/images/mathukai-organic-logo-transparent.png')}
            style={styles.logo}
          />
          <View>
            <Text style={styles.eyebrow}>MATHUKAI STAFF</Text>
            <Text style={styles.title}>{tabTitle(activeTab)}</Text>
          </View>
        </View>
      </View>

      <View style={styles.content}>
        {activeTab === 'home' ? <StaffHomeScreen onOpenOrders={openOrders} onOpenStock={() => setActiveTab('stock')} /> : null}
        {activeTab === 'orders' ? <OrdersScreen key={`orders-${ordersKey}`} initialFilters={orderFilters} /> : null}
        {activeTab === 'stock' ? <StockScreen /> : null}
        {activeTab === 'account' ? <AccountScreen /> : null}
      </View>

      <View style={styles.bottomTabs}>
        {TABS.map((tab) => {
          const active = tab.key === activeTab;
          return (
            <Pressable key={tab.key} onPress={() => setActiveTab(tab.key)} style={styles.tabButton}>
              <MaterialCommunityIcons name={tab.icon} size={24} color={active ? '#006B44' : '#70867D'} />
              <Text style={[styles.tabLabel, active && styles.tabLabelActive]}>{tab.label}</Text>
            </Pressable>
          );
        })}
      </View>
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  shell: { flex: 1, backgroundColor: '#F7FAF8' },
  header: { borderBottomWidth: 1, borderBottomColor: '#DCE7E1', backgroundColor: '#FFFFFF', paddingHorizontal: 16, paddingVertical: 12 },
  brandRow: { flexDirection: 'row', alignItems: 'center', gap: 12 },
  logo: { width: 42, height: 42 },
  eyebrow: { color: '#6B8177', fontSize: 10, fontWeight: '800', letterSpacing: 1.2 },
  title: { color: '#17352A', fontSize: 20, fontWeight: '900', marginTop: 2 },
  content: { flex: 1 },
  bottomTabs: { flexDirection: 'row', borderTopWidth: 1, borderTopColor: '#DCE7E1', backgroundColor: '#FFFFFF', paddingTop: 8, paddingBottom: 10 },
  tabButton: { flex: 1, alignItems: 'center', justifyContent: 'center', gap: 3 },
  tabLabel: { color: '#70867D', fontSize: 12, fontWeight: '700' },
  tabLabelActive: { color: '#006B44' },
  center: { flex: 1, alignItems: 'center', justifyContent: 'center', paddingHorizontal: 26 },
  loadingText: { color: '#587066', marginTop: 12, fontWeight: '700' },
  errorTitle: { color: '#17352A', fontSize: 20, fontWeight: '900', textAlign: 'center' },
  errorMessage: { color: '#587066', marginTop: 8, textAlign: 'center', lineHeight: 21 },
  retryButton: { marginTop: 18, backgroundColor: '#0B5D3B', minHeight: 46, borderRadius: 14, paddingHorizontal: 20, alignItems: 'center', justifyContent: 'center' },
  retryText: { color: '#FFFFFF', fontWeight: '900' },
  homeScroll: { padding: 16, paddingBottom: 26, gap: 14 },
  warning: { borderWidth: 1, borderColor: '#F5D48B', backgroundColor: '#FFF8E8', borderRadius: 14, padding: 12 },
  warningText: { color: '#7B5A12', lineHeight: 19, fontWeight: '700' },
  storeCard: { flexDirection: 'row', alignItems: 'center', gap: 14, backgroundColor: '#FFFFFF', borderWidth: 1, borderColor: '#DCE7E1', borderRadius: 22, padding: 16, shadowColor: '#17352A', shadowOffset: { width: 0, height: 4 }, shadowOpacity: 0.06, shadowRadius: 8, elevation: 2 },
  storeIcon: { width: 54, height: 54, borderRadius: 18, backgroundColor: '#EAF6EF', alignItems: 'center', justifyContent: 'center' },
  storeCopy: { flex: 1 },
  storeLabel: { color: '#70867D', fontSize: 12, fontWeight: '800', textTransform: 'uppercase', letterSpacing: 0.7 },
  storeName: { color: '#17352A', fontSize: 22, fontWeight: '900', marginTop: 2 },
  staffNameRow: { flexDirection: 'row', alignItems: 'center', gap: 5, marginTop: 7 },
  staffName: { color: '#29483D', fontSize: 13, fontWeight: '800' },
  updatedText: { color: '#71867D', fontSize: 12, marginTop: 4 },
  monthCard: { flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', backgroundColor: '#F0F8F3', borderColor: '#CFE6D8', borderWidth: 1, borderRadius: 18, paddingHorizontal: 15, paddingVertical: 13 },
  monthLabel: { color: '#587066', fontSize: 12, fontWeight: '800' },
  monthValue: { color: '#17352A', fontSize: 17, fontWeight: '900', marginTop: 2 },
  monthButton: { flexDirection: 'row', alignItems: 'center', gap: 4, borderWidth: 1, borderColor: '#B9DDBF', backgroundColor: '#FFFFFF', borderRadius: 14, paddingHorizontal: 12, minHeight: 38 },
  monthButtonText: { color: '#0B5D3B', fontWeight: '900' },
  sectionTitle: { color: '#17352A', fontSize: 19, fontWeight: '900', marginTop: 4 },
  totalOrdersCard: { flexDirection: 'row', alignItems: 'center', gap: 14, backgroundColor: '#FFFFFF', borderColor: '#DCE7E1', borderWidth: 1, borderRadius: 20, padding: 16 },
  totalOrdersIcon: { width: 58, height: 58, borderRadius: 18, backgroundColor: '#EAF6EF', alignItems: 'center', justifyContent: 'center' },
  totalOrdersLabel: { color: '#587066', fontSize: 14, fontWeight: '800' },
  totalOrdersValue: { color: '#0B5D3B', fontSize: 34, fontWeight: '900', lineHeight: 38 },
  pipelineHeading: { marginTop: 8 },
  metricGrid: { flexDirection: 'row', flexWrap: 'wrap', justifyContent: 'space-between', rowGap: 10 },
  metricCard: { width: '48.5%', minHeight: 91, backgroundColor: '#FFFFFF', borderRadius: 16, borderColor: '#DFE5E2', borderWidth: 1, padding: 11, flexDirection: 'row', alignItems: 'center', shadowColor: '#17352A', shadowOffset: { width: 0, height: 2 }, shadowOpacity: 0.05, shadowRadius: 5, elevation: 1 },
  metricIcon: { width: 43, height: 43, borderRadius: 12, borderWidth: 1, alignItems: 'center', justifyContent: 'center' },
  metricCopy: { flex: 1, marginLeft: 9 },
  metricValue: { fontSize: 25, fontWeight: '900', marginTop: 2 },
  metricLabel: { color: '#455A51', fontSize: 12, fontWeight: '700' },
  attentionCard: { flexDirection: 'row', alignItems: 'center', gap: 12, backgroundColor: '#FFF8E8', borderWidth: 1, borderColor: '#F5D48B', borderRadius: 18, padding: 14 },
  attentionIcon: { width: 48, height: 48, borderRadius: 16, backgroundColor: '#FFF1C2', alignItems: 'center', justifyContent: 'center' },
  attentionCopy: { flex: 1 },
  attentionTitle: { color: '#3C2D0A', fontSize: 15, fontWeight: '900' },
  attentionText: { color: '#7B5A12', marginTop: 3, lineHeight: 19 },
  noAttentionCard: { flexDirection: 'row', alignItems: 'center', gap: 10, backgroundColor: '#EAF6EF', borderColor: '#B9DDBF', borderWidth: 1, borderRadius: 18, padding: 14 },
  noAttentionText: { color: '#0B5D3B', fontWeight: '800' },
  modalBackdrop: { flex: 1, backgroundColor: 'rgba(15,35,28,0.52)', justifyContent: 'center', padding: 24 },
  modalDismiss: { position: 'absolute', inset: 0 },
  monthPickerCard: { backgroundColor: '#FFFFFF', borderRadius: 22, padding: 16, maxHeight: '78%' },
  monthPickerTitle: { color: '#17352A', fontSize: 21, fontWeight: '900', marginBottom: 12 },
  monthOption: { minHeight: 50, borderBottomColor: '#E7ECEA', borderBottomWidth: 1, paddingHorizontal: 10, flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between' },
  monthOptionSelected: { backgroundColor: '#EAF6EF', borderRadius: 12 },
  monthOptionText: { color: '#40564D', fontSize: 15, fontWeight: '700' },
  monthOptionTextSelected: { color: '#0B5D3B', fontWeight: '900' },
  accountPage: { flex: 1, padding: 16, gap: 14 },
  accountCard: { alignItems: 'center', backgroundColor: '#FFFFFF', borderColor: '#DCE7E1', borderWidth: 1, borderRadius: 22, padding: 24 },
  avatar: { width: 70, height: 70, borderRadius: 35, alignItems: 'center', justifyContent: 'center', backgroundColor: '#E4F5EC', marginBottom: 12 },
  avatarText: { color: '#006B44', fontSize: 30, fontWeight: '900' },
  accountName: { color: '#17352A', fontSize: 22, fontWeight: '900' },
  accountMeta: { color: '#587066', marginTop: 6, fontSize: 15 },
  accountRole: { color: '#006B44', marginTop: 10, fontWeight: '800' },
  infoCard: { backgroundColor: '#FFFDF5', borderColor: '#F5D48B', borderWidth: 1, borderRadius: 18, padding: 16 },
  infoTitle: { color: '#3C2D0A', fontSize: 16, fontWeight: '900', marginBottom: 6 },
  infoText: { color: '#6B5B32', lineHeight: 21 },
  signOutButton: { minHeight: 52, borderRadius: 16, borderWidth: 1, borderColor: '#F3B8B4', backgroundColor: '#FFF5F4', flexDirection: 'row', alignItems: 'center', justifyContent: 'center', gap: 8 },
  signOutText: { color: '#B42318', fontSize: 16, fontWeight: '900' },
  pressed: { opacity: 0.65 },
});
