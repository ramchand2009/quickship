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
import type { DashboardResponse } from '../auth/types';
import OrdersScreen from '../orders/OrdersScreen';
import type { OrderListFilters, OrderSummary } from '../orders/types';
import NotificationBridge from '../notifications/NotificationBridge';
import NotificationsScreen from '../notifications/NotificationsScreen';
import StockScreen from '../stock/StockScreen';

type AppTab = 'dashboard' | 'orders' | 'stock' | 'account' | 'notifications';
type TabIconName = keyof typeof MaterialCommunityIcons.glyphMap;

const TABS: { key: AppTab; label: string; icon: TabIconName; activeIcon: TabIconName }[] = [
  { key: 'dashboard', label: 'Home', icon: 'home-outline', activeIcon: 'home' },
  { key: 'orders', label: 'Orders', icon: 'clipboard-text-outline', activeIcon: 'clipboard-text' },
  { key: 'stock', label: 'Stock', icon: 'package-variant-closed', activeIcon: 'package-variant' },
  { key: 'account', label: 'Account', icon: 'account-circle-outline', activeIcon: 'account-circle' },
];

const METRIC_ICONS: Record<string, TabIconName> = {
  total_orders: 'clipboard-list-outline',
  pending_orders: 'clock-outline',
  accepted_orders: 'clipboard-check-outline',
  shipped_orders: 'truck-delivery-outline',
  completed_orders: 'package-variant-closed-check',
  cancelled_orders: 'close-circle-outline',
};

const METRIC_COLORS: Record<string, { foreground: string; background: string; border: string }> = {
  total_orders: { foreground: '#14733D', background: '#ECF7EE', border: '#B9DDBF' },
  pending_orders: { foreground: '#E68200', background: '#FFF7E8', border: '#F3D28B' },
  accepted_orders: { foreground: '#14733D', background: '#ECF7EE', border: '#B9DDBF' },
  shipped_orders: { foreground: '#1769C2', background: '#EFF6FF', border: '#B6D7FF' },
  completed_orders: { foreground: '#14733D', background: '#ECF7EE', border: '#B9DDBF' },
  cancelled_orders: { foreground: '#D92D3A', background: '#FFF1F2', border: '#FFC2C7' },
};

function formatUpdatedAt(value?: string) {
  if (!value) return '';
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return '';
  return parsed.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
}

function formatMetricValue(value: number | string) {
  return String(value).replace(/^â‚¹\s*/, '₹');
}

function monthOrderFilters(): OrderListFilters {
  const now = new Date();
  const start = new Date(now.getFullYear(), now.getMonth(), 1);
  const end = new Date(now.getFullYear(), now.getMonth() + 1, 0);
  const localDate = (value: Date) => {
    const year = value.getFullYear();
    const month = String(value.getMonth() + 1).padStart(2, '0');
    const day = String(value.getDate()).padStart(2, '0');
    return `${year}-${month}-${day}`;
  };
  return { date_from: localDate(start), date_to: localDate(end) };
}

function formatMoney(order: OrderSummary) {
  const amount = Number(order.total.amount || 0).toLocaleString('en-IN', {
    minimumFractionDigits: 0,
    maximumFractionDigits: 2,
  });
  return order.total.currency === 'INR' ? `₹${amount}` : `${order.total.currency} ${amount}`;
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

function orderIdFromDestination(destination: string) {
  const match = destination.match(/^\/orders\/(\d+)$/);
  return match ? Number(match[1]) : null;
}

function DashboardScreen({ onNavigate }: { onNavigate: (destination: string) => void }) {
  const { runAuthenticated } = useAuth();
  const [dashboard, setDashboard] = useState<DashboardResponse | null>(null);
  const [recentOrders, setRecentOrders] = useState<OrderSummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState('');

  const loadDashboard = useCallback(async (refresh = false) => {
    if (refresh) setRefreshing(true); else setLoading(true);
    setError('');
    try {
      setDashboard(await runAuthenticated(api.dashboard));
      try {
        const orders = await runAuthenticated((token) => api.orders(token, monthOrderFilters()));
        setRecentOrders(orders.data.slice(0, 2));
      } catch {
        // The monthly summary remains usable when this optional preview fails.
      }
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

  const financeMetrics = dashboard.data.metrics.filter((metric) => metric.key === 'total_sales' || metric.key === 'total_profit');
  const orderMetrics = dashboard.data.metrics.filter((metric) => METRIC_ICONS[metric.key]);

  return (
    <ScrollView
      contentContainerStyle={styles.scrollContent}
      refreshControl={<RefreshControl refreshing={refreshing} onRefresh={() => void loadDashboard(true)} colors={['#0B5D3B']} tintColor="#0B5D3B" />}
    >
      {error ? <View style={styles.warning}><Text style={styles.warningText}>{error} Showing the last loaded data.</Text></View> : null}

      <View style={styles.periodRow}>
        <Text style={styles.updatedText}>Updated {formatUpdatedAt(dashboard.meta.server_time) || 'recently'}</Text>
        <View style={styles.periodPicker}>
          <Text style={styles.periodPickerText}>This month</Text>
          <MaterialCommunityIcons color="#375047" name="chevron-down" size={20} />
        </View>
      </View>

      <View style={styles.financeCard}>
        {financeMetrics.map((metric, index) => (
          <Pressable
            key={metric.key}
            onPress={() => onNavigate(metric.destination)}
            style={({ pressed }) => [styles.financeMetric, index > 0 && styles.financeMetricBorder, pressed && styles.pressed]}
          >
            <View style={styles.financeIcon}>
              <MaterialCommunityIcons color="#14733D" name={metric.key === 'total_sales' ? 'finance' : 'currency-inr'} size={25} />
            </View>
            <View style={styles.financeCopy}>
              <Text style={styles.financeLabel}>{metric.key === 'total_profit' ? 'Profit' : metric.label}</Text>
              <Text numberOfLines={1} adjustsFontSizeToFit style={styles.financeValue}>{formatMetricValue(metric.value)}</Text>
            </View>
          </Pressable>
        ))}
      </View>

      <View style={styles.metricGrid}>
        {orderMetrics.map((metric) => {
          const colors = METRIC_COLORS[metric.key] || METRIC_COLORS.total_orders;
          return (
            <Pressable
              accessibilityRole="button"
              key={metric.key}
              onPress={() => onNavigate(metric.destination)}
              style={({ pressed }) => [styles.metricCard, pressed && styles.pressed]}
            >
              <View style={[styles.metricIcon, { backgroundColor: colors.background, borderColor: colors.border }]}>
                <MaterialCommunityIcons color={colors.foreground} name={METRIC_ICONS[metric.key]} size={25} />
              </View>
              <View style={styles.metricCopy}>
                <Text style={styles.metricLabel}>{metric.label}</Text>
                <Text style={[styles.metricValue, { color: colors.foreground }]}>{formatMetricValue(metric.value)}</Text>
              </View>
              <MaterialCommunityIcons color="#52665E" name="chevron-right" size={22} />
            </Pressable>
          );
        })}
      </View>

      {dashboard.data.alerts.map((alert) => (
        <Pressable
          key={alert.id}
          onPress={() => onNavigate(alert.destination)}
          style={({ pressed }) => [styles.alertCard, pressed && styles.pressed]}
        >
          <View style={styles.alertIcon}><MaterialCommunityIcons color="#D98200" name="alert" size={23} /></View>
          <View style={styles.alertCopy}>
            <Text style={styles.alertTitle}>{alert.title}</Text>
            <Text style={styles.alertMessage}>{alert.message}</Text>
          </View>
          <MaterialCommunityIcons color="#52665E" name="chevron-right" size={25} />
        </Pressable>
      ))}

      <View style={styles.recentHeading}>
        <Text style={styles.sectionTitle}>Recent orders</Text>
        <Pressable onPress={() => onNavigate('/orders')} style={styles.viewAllButton}>
          <Text style={styles.viewAllText}>View all</Text>
          <MaterialCommunityIcons color="#0B5D3B" name="chevron-right" size={21} />
        </Pressable>
      </View>

      {recentOrders.length ? recentOrders.map((order) => {
        const paymentStyle = order.payment_state.code === 'received' || order.payment_state.code === 'paid'
          ? styles.orderPillSuccess
          : styles.orderPillAttention;
        const orderStatusStyle = order.status.code === 'shipped'
          ? styles.orderPillInfo
          : order.status.code === 'cancelled'
            ? styles.orderPillCritical
            : styles.orderPillSuccess;
        return (
          <Pressable
            key={order.id}
            onPress={() => onNavigate(`/orders/${order.id}`)}
            style={({ pressed }) => [styles.recentOrderCard, pressed && styles.pressed]}
          >
            <View style={styles.orderThumbnail}><MaterialCommunityIcons color="#14733D" name="package-variant" size={30} /></View>
            <View style={styles.orderSummary}>
              <Text numberOfLines={1} style={styles.orderReference}>{order.reference}</Text>
              <View style={styles.orderCustomerRow}>
                <MaterialCommunityIcons color="#6F7F78" name="account-outline" size={17} />
                <Text numberOfLines={1} style={styles.orderCustomer}>{order.customer_display_name || 'Customer'}</Text>
              </View>
              <View style={styles.orderPills}>
                <View style={[styles.orderPill, paymentStyle]}><Text style={styles.orderPillText}>{order.payment_state.label}</Text></View>
                <View style={[styles.orderPill, orderStatusStyle]}><Text style={styles.orderPillText}>{order.status.label}</Text></View>
              </View>
            </View>
            <View style={styles.orderPriceColumn}>
              <Text style={styles.orderPrice}>{formatMoney(order)}</Text>
              <MaterialCommunityIcons color="#52665E" name="chevron-right" size={24} />
            </View>
          </Pressable>
        );
      }) : (
        <View style={styles.emptyRecentCard}>
          <MaterialCommunityIcons color="#819189" name="package-variant-closed" size={28} />
          <Text style={styles.emptyRecentText}>No orders found for this month.</Text>
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
      <View style={styles.profileCard}>
        <View style={styles.avatar}>
          <Text style={styles.avatarText}>{auth.session.user.display_name.slice(0, 1).toUpperCase()}</Text>
          <View style={styles.onlineBadge} />
        </View>
        <View style={styles.profileCopy}>
          <Text style={styles.profileName}>{auth.session.user.display_name}</Text>
          <Text style={styles.profileUsername}>@{auth.session.user.username}</Text>
          <View style={styles.roleBadge}>
            <MaterialCommunityIcons color="#14733D" name="shield-account-outline" size={14} />
            <Text style={styles.roleBadgeText}>{auth.session.active_tenant.role_label}</Text>
          </View>
        </View>
      </View>

      <Text style={styles.accountSectionTitle}>Workspace</Text>
      <View style={styles.detailCard}>
        <View style={styles.accountDetailRow}>
          <View style={styles.accountDetailIcon}><MaterialCommunityIcons color="#14733D" name="store-outline" size={22} /></View>
          <View style={styles.accountDetailCopy}>
            <Text style={styles.detailLabel}>Business workspace</Text>
            <Text style={styles.detailValue}>{auth.session.active_tenant.tenant_name}</Text>
          </View>
        </View>
        <View style={styles.divider} />
        <View style={styles.accountDetailRow}>
          <View style={styles.accountDetailIcon}><MaterialCommunityIcons color="#14733D" name="account-key-outline" size={22} /></View>
          <View style={styles.accountDetailCopy}>
            <Text style={styles.detailLabel}>Access role</Text>
            <Text style={styles.detailValue}>{auth.session.active_tenant.role_label}</Text>
          </View>
        </View>
        <View style={styles.divider} />
        <View style={styles.accountDetailRow}>
          <View style={styles.accountDetailIcon}><MaterialCommunityIcons color="#14733D" name="key-chain-variant" size={22} /></View>
          <View style={styles.accountDetailCopy}>
            <Text style={styles.detailLabel}>Granted permissions</Text>
            <Text style={styles.detailValue}>{auth.session.permissions.length} active permissions</Text>
          </View>
        </View>
      </View>

      <Text style={styles.accountSectionTitle}>Application</Text>
      <View style={styles.appInfoCard}>
        <View style={styles.appMark}><Text style={styles.appMarkText}>M</Text></View>
        <View style={styles.appInfoCopy}>
          <Text style={styles.appInfoName}>Mathukai Operations</Text>
          <Text style={styles.appInfoVersion}>Android · Version 1.0.0</Text>
        </View>
        <View style={styles.liveSessionBadge}><View style={styles.liveSessionDot} /><Text style={styles.liveSessionText}>Connected</Text></View>
      </View>

      <View style={styles.securityNote}>
        <MaterialCommunityIcons color="#6B5B22" name="shield-check-outline" size={22} />
        <Text style={styles.securityNoteText}>Your session is secured on this device. Sign out before sharing the device.</Text>
      </View>

      <Pressable disabled={busy} onPress={() => void signOutNow()} style={({ pressed }) => [styles.signOutButton, (pressed || busy) && styles.pressed]}>
        {busy ? <ActivityIndicator color="#B42318" /> : (
          <>
            <MaterialCommunityIcons color="#B42318" name="logout" size={21} />
            <Text style={styles.signOutText}>Sign out</Text>
          </>
        )}
      </Pressable>
    </ScrollView>
  );
}

export default function OperationsApp() {
  const { auth, runAuthenticated } = useAuth();
  const [activeTab, setActiveTab] = useState<AppTab>('dashboard');
  const [ordersInitialFilters, setOrdersInitialFilters] = useState<OrderListFilters>({});
  const [ordersInitialOrderId, setOrdersInitialOrderId] = useState<number | null>(null);
  const [ordersScreenKey, setOrdersScreenKey] = useState(0);
  const [unreadNotificationCount, setUnreadNotificationCount] = useState(0);

  const refreshUnreadCount = useCallback(async () => {
    if (!auth?.session.active_tenant) return;
    try {
      const response = await runAuthenticated((token) => api.notifications(token, { unread_only: true, page_size: 1 }));
      setUnreadNotificationCount(response.meta.unread_count || 0);
    } catch {
      // The rest of the app remains usable when notification summary refresh fails.
    }
  }, [auth?.session.active_tenant, runAuthenticated]);

  useEffect(() => { void refreshUnreadCount(); }, [refreshUnreadCount]);

  const openDestination = useCallback((destination: string) => {
    if (destination.startsWith('/products')) {
      setActiveTab('stock');
      return;
    }
    setOrdersInitialOrderId(orderIdFromDestination(destination));
    setOrdersInitialFilters(orderFiltersFromDestination(destination));
    setOrdersScreenKey((current) => current + 1);
    setActiveTab('orders');
  }, []);

  const openTab = useCallback((tab: AppTab) => {
    if (tab === 'orders') {
      setOrdersInitialFilters({});
      setOrdersInitialOrderId(null);
      setOrdersScreenKey((current) => current + 1);
    }
    setActiveTab(tab);
  }, []);
  const title = useMemo(() => {
    if (activeTab === 'dashboard') {
      const firstName = (auth?.session.user.display_name || '').trim().split(/\s+/)[0];
      return `Good morning${firstName ? `, ${firstName}` : ''}`;
    }
    if (activeTab === 'notifications') return 'Notifications';
    return TABS.find((tab) => tab.key === activeTab)?.label || '';
  }, [activeTab, auth?.session.user.display_name]);

  return (
    <SafeAreaView style={styles.app}>
      <View style={styles.header}>
        <View>
          <Text style={styles.headerEyebrow}>{auth?.session.active_tenant?.tenant_name}</Text>
          <Text style={styles.headerTitle}>{title}</Text>
        </View>
        <View style={styles.headerActions}>
          <Pressable
            accessibilityLabel="Open notifications"
            onPress={() => setActiveTab('notifications')}
            style={({ pressed }) => [styles.notificationButton, pressed && styles.pressed]}
          >
            <MaterialCommunityIcons color="#FFFFFF" name={unreadNotificationCount ? 'bell' : 'bell-outline'} size={25} />
            {unreadNotificationCount ? (
              <View style={styles.notificationBadge}>
                <Text style={styles.notificationBadgeText}>{unreadNotificationCount > 99 ? '99+' : unreadNotificationCount}</Text>
              </View>
            ) : null}
          </Pressable>
          <View style={styles.liveBadge}><View style={styles.liveDot} /><Text style={styles.liveText}>LIVE</Text></View>
        </View>
      </View>

      <View style={styles.content}>
        <NotificationBridge onDestination={openDestination} onNotificationReceived={refreshUnreadCount} />
        {activeTab === 'dashboard' ? <DashboardScreen onNavigate={openDestination} /> : null}
        {activeTab === 'orders' ? <OrdersScreen initialFilters={ordersInitialFilters} initialOrderId={ordersInitialOrderId} key={ordersScreenKey} /> : null}
        {activeTab === 'stock' ? <StockScreen /> : null}
        {activeTab === 'account' ? <AccountScreen /> : null}
        {activeTab === 'notifications' ? <NotificationsScreen onOpenDestination={openDestination} onUnreadCountChange={setUnreadNotificationCount} /> : null}
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
  header: { minHeight: 112, backgroundColor: '#075C38', paddingHorizontal: 20, paddingVertical: 17, flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', borderBottomLeftRadius: 22, borderBottomRightRadius: 22 },
  headerEyebrow: { color: '#BFE2D2', fontSize: 12, fontWeight: '800', letterSpacing: 0.8, textTransform: 'uppercase' },
  headerTitle: { color: '#FFFFFF', fontSize: 23, fontWeight: '800', marginTop: 8 },
  headerActions: { flexDirection: 'row', alignItems: 'center', columnGap: 8 },
  notificationButton: { width: 42, height: 42, borderRadius: 21, backgroundColor: '#174E36', alignItems: 'center', justifyContent: 'center' },
  notificationBadge: { position: 'absolute', top: -4, right: -5, minWidth: 20, height: 20, borderRadius: 10, backgroundColor: '#D92D20', borderColor: '#0B5D3B', borderWidth: 2, paddingHorizontal: 4, alignItems: 'center', justifyContent: 'center' },
  notificationBadgeText: { color: '#FFFFFF', fontSize: 9, fontWeight: '900' },
  liveBadge: { backgroundColor: '#174E36', borderRadius: 20, flexDirection: 'row', alignItems: 'center', paddingHorizontal: 10, paddingVertical: 7 },
  liveDot: { width: 7, height: 7, borderRadius: 4, backgroundColor: '#65D49B', marginRight: 6 },
  liveText: { color: '#FFFFFF', fontSize: 10, fontWeight: '900', letterSpacing: 0.8 },
  content: { flex: 1 },
  scrollContent: { padding: 16, paddingBottom: 28 },
  center: { flex: 1, alignItems: 'center', justifyContent: 'center', padding: 30 },
  loadingText: { color: '#587066', marginTop: 14, fontWeight: '600' },
  warning: { backgroundColor: '#FFF4D8', borderColor: '#F0D08D', borderWidth: 1, borderRadius: 12, padding: 12, marginBottom: 16 },
  warningText: { color: '#7A4A00', lineHeight: 19 },
  sectionTitle: { color: '#17352A', fontSize: 20, fontWeight: '800' },
  updatedText: { color: '#71867D', fontSize: 12 },
  periodRow: { minHeight: 42, flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', marginBottom: 12 },
  periodPicker: { minHeight: 39, borderColor: '#C9D2CD', borderWidth: 1, borderRadius: 11, paddingHorizontal: 12, flexDirection: 'row', alignItems: 'center', columnGap: 5, backgroundColor: '#FFFFFF' },
  periodPickerText: { color: '#223B31', fontSize: 14, fontWeight: '700' },
  financeCard: { minHeight: 112, backgroundColor: '#FFFFFF', borderColor: '#DFE5E2', borderWidth: 1, borderRadius: 18, padding: 14, flexDirection: 'row', alignItems: 'stretch', shadowColor: '#17352A', shadowOffset: { width: 0, height: 3 }, shadowOpacity: 0.08, shadowRadius: 8, elevation: 2, marginBottom: 14 },
  financeMetric: { flex: 1, flexDirection: 'row', alignItems: 'center', paddingHorizontal: 3 },
  financeMetricBorder: { borderLeftColor: '#E2E7E4', borderLeftWidth: 1, paddingLeft: 12, marginLeft: 7 },
  financeIcon: { width: 42, height: 42, borderRadius: 21, backgroundColor: '#E4F3E8', alignItems: 'center', justifyContent: 'center' },
  financeCopy: { flex: 1, marginLeft: 9 },
  financeLabel: { color: '#64746D', fontSize: 12, fontWeight: '700' },
  financeValue: { color: '#075C38', fontSize: 20, fontWeight: '900', marginTop: 5 },
  metricGrid: { flexDirection: 'row', flexWrap: 'wrap', justifyContent: 'space-between', rowGap: 10 },
  metricCard: { width: '48.5%', minHeight: 91, backgroundColor: '#FFFFFF', borderRadius: 16, borderColor: '#DFE5E2', borderWidth: 1, padding: 11, flexDirection: 'row', alignItems: 'center', shadowColor: '#17352A', shadowOffset: { width: 0, height: 2 }, shadowOpacity: 0.05, shadowRadius: 5, elevation: 1 },
  metricIcon: { width: 43, height: 43, borderRadius: 12, borderWidth: 1, alignItems: 'center', justifyContent: 'center' },
  metricCopy: { flex: 1, marginLeft: 9 },
  metricValue: { fontSize: 25, fontWeight: '900', marginTop: 2 },
  metricLabel: { color: '#455A51', fontSize: 12, fontWeight: '700' },
  alertCard: { backgroundColor: '#FFF9EB', borderColor: '#EDC976', borderWidth: 1, borderRadius: 14, padding: 12, marginTop: 14, flexDirection: 'row', alignItems: 'center' },
  alertIcon: { width: 35, height: 35, borderRadius: 10, backgroundColor: '#FFF1C9', alignItems: 'center', justifyContent: 'center', marginRight: 10 },
  alertCopy: { flex: 1 },
  alertTitle: { color: '#453814', fontWeight: '800', fontSize: 14 },
  alertMessage: { color: '#78652E', lineHeight: 17, marginTop: 2, fontSize: 12 },
  recentHeading: { flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', marginTop: 22, marginBottom: 10 },
  viewAllButton: { minHeight: 38, flexDirection: 'row', alignItems: 'center', paddingLeft: 10 },
  viewAllText: { color: '#0B5D3B', fontSize: 14, fontWeight: '800' },
  recentOrderCard: { minHeight: 112, backgroundColor: '#FFFFFF', borderColor: '#DFE5E2', borderWidth: 1, borderRadius: 16, padding: 11, marginBottom: 10, flexDirection: 'row', alignItems: 'center', shadowColor: '#17352A', shadowOffset: { width: 0, height: 2 }, shadowOpacity: 0.05, shadowRadius: 6, elevation: 1 },
  orderThumbnail: { width: 66, height: 76, borderRadius: 13, backgroundColor: '#EEF5EF', alignItems: 'center', justifyContent: 'center' },
  orderSummary: { flex: 1, marginLeft: 12 },
  orderReference: { color: '#152C23', fontSize: 16, fontWeight: '900' },
  orderCustomerRow: { flexDirection: 'row', alignItems: 'center', marginTop: 5 },
  orderCustomer: { color: '#62736B', fontSize: 13, marginLeft: 4, flex: 1 },
  orderPills: { flexDirection: 'row', flexWrap: 'wrap', gap: 6, marginTop: 8 },
  orderPill: { borderRadius: 12, paddingHorizontal: 9, paddingVertical: 4 },
  orderPillSuccess: { backgroundColor: '#E7F6E8' },
  orderPillAttention: { backgroundColor: '#FFF1D7' },
  orderPillInfo: { backgroundColor: '#E7F1FF' },
  orderPillCritical: { backgroundColor: '#FFE8EA' },
  orderPillText: { color: '#244038', fontSize: 11, fontWeight: '800' },
  orderPriceColumn: { minWidth: 65, alignSelf: 'stretch', alignItems: 'flex-end', justifyContent: 'space-between', paddingVertical: 4 },
  orderPrice: { color: '#162921', fontSize: 15, fontWeight: '900' },
  emptyRecentCard: { minHeight: 82, backgroundColor: '#FFFFFF', borderColor: '#DFE5E2', borderWidth: 1, borderRadius: 15, flexDirection: 'row', alignItems: 'center', justifyContent: 'center', padding: 16 },
  emptyRecentText: { color: '#6B7C74', marginLeft: 9, fontWeight: '600' },
  clearCard: { backgroundColor: '#E4F3EB', borderRadius: 15, padding: 17 },
  clearTitle: { color: '#174E36', fontSize: 16, fontWeight: '800' },
  clearMessage: { color: '#3D6958', lineHeight: 20, marginTop: 5 },
  errorTitle: { color: '#17352A', fontSize: 21, fontWeight: '800', textAlign: 'center' },
  errorMessage: { color: '#587066', lineHeight: 21, textAlign: 'center', marginTop: 8 },
  retryButton: { backgroundColor: '#0B5D3B', minHeight: 48, borderRadius: 13, paddingHorizontal: 24, alignItems: 'center', justifyContent: 'center', marginTop: 20 },
  retryText: { color: '#FFFFFF', fontWeight: '800' },
  profileCard: { minHeight: 118, backgroundColor: '#075C38', borderRadius: 20, padding: 18, flexDirection: 'row', alignItems: 'center', shadowColor: '#17352A', shadowOffset: { width: 0, height: 3 }, shadowOpacity: 0.13, shadowRadius: 8, elevation: 3 },
  avatar: { width: 64, height: 64, borderRadius: 32, backgroundColor: '#FFFFFF', alignItems: 'center', justifyContent: 'center' },
  avatarText: { color: '#075C38', fontSize: 27, fontWeight: '900' },
  onlineBadge: { position: 'absolute', right: 1, bottom: 2, width: 15, height: 15, borderRadius: 8, backgroundColor: '#66D49B', borderColor: '#075C38', borderWidth: 3 },
  profileCopy: { flex: 1, marginLeft: 15 },
  profileName: { color: '#FFFFFF', fontSize: 20, fontWeight: '900' },
  profileUsername: { color: '#C5E3D5', marginTop: 3 },
  roleBadge: { alignSelf: 'flex-start', backgroundColor: '#E8F5EB', borderRadius: 13, paddingHorizontal: 9, paddingVertical: 4, flexDirection: 'row', alignItems: 'center', columnGap: 5, marginTop: 9 },
  roleBadgeText: { color: '#14733D', fontSize: 10, fontWeight: '900' },
  accountSectionTitle: { color: '#17352A', fontSize: 16, fontWeight: '900', marginTop: 22, marginBottom: 10 },
  detailCard: { backgroundColor: '#FFFFFF', borderColor: '#DFE5E2', borderWidth: 1, borderRadius: 17, padding: 16 },
  accountDetailRow: { flexDirection: 'row', alignItems: 'center' },
  accountDetailIcon: { width: 42, height: 42, borderRadius: 12, backgroundColor: '#ECF7EE', alignItems: 'center', justifyContent: 'center' },
  accountDetailCopy: { flex: 1, marginLeft: 12 },
  detailLabel: { color: '#71867D', fontSize: 12, fontWeight: '700', textTransform: 'uppercase', letterSpacing: 0.7 },
  detailValue: { color: '#17352A', fontSize: 16, fontWeight: '800', marginTop: 5 },
  divider: { height: 1, backgroundColor: '#E4EAE7', marginVertical: 16 },
  appInfoCard: { minHeight: 78, backgroundColor: '#FFFFFF', borderColor: '#DFE5E2', borderWidth: 1, borderRadius: 17, padding: 13, flexDirection: 'row', alignItems: 'center' },
  appMark: { width: 48, height: 48, borderRadius: 14, backgroundColor: '#075C38', alignItems: 'center', justifyContent: 'center' },
  appMarkText: { color: '#FFFFFF', fontSize: 22, fontWeight: '900' },
  appInfoCopy: { flex: 1, marginLeft: 11 },
  appInfoName: { color: '#17352A', fontSize: 14, fontWeight: '900' },
  appInfoVersion: { color: '#71867D', fontSize: 11, marginTop: 4 },
  liveSessionBadge: { backgroundColor: '#E7F6E8', borderRadius: 13, paddingHorizontal: 9, paddingVertical: 6, flexDirection: 'row', alignItems: 'center' },
  liveSessionDot: { width: 6, height: 6, borderRadius: 3, backgroundColor: '#14733D', marginRight: 5 },
  liveSessionText: { color: '#14733D', fontSize: 9, fontWeight: '900' },
  securityNote: { backgroundColor: '#FFF9E9', borderColor: '#EDD28B', borderWidth: 1, borderRadius: 14, padding: 13, flexDirection: 'row', alignItems: 'center', marginTop: 16 },
  securityNoteText: { color: '#6B5B22', fontSize: 12, lineHeight: 18, flex: 1, marginLeft: 9 },
  signOutButton: { minHeight: 52, borderColor: '#E5AAA5', borderWidth: 1, borderRadius: 14, flexDirection: 'row', alignItems: 'center', justifyContent: 'center', columnGap: 8, marginTop: 18 },
  signOutText: { color: '#B42318', fontWeight: '800' },
  tabBar: { minHeight: 76, backgroundColor: '#FFFFFF', borderTopColor: '#DCE5E1', borderTopWidth: 1, flexDirection: 'row', paddingHorizontal: 6, shadowColor: '#17352A', shadowOffset: { width: 0, height: -2 }, shadowOpacity: 0.06, shadowRadius: 6, elevation: 5 },
  tab: { flex: 1, alignItems: 'center', justifyContent: 'center' },
  tabIndicator: { width: 22, height: 3, borderRadius: 2, backgroundColor: 'transparent', marginBottom: 4 },
  tabIndicatorActive: { backgroundColor: '#0B5D3B' },
  tabIcon: { marginBottom: 2 },
  tabText: { color: '#71867D', fontSize: 12, fontWeight: '700' },
  tabTextActive: { color: '#0B5D3B', fontWeight: '900' },
  pressed: { opacity: 0.65 },
});
