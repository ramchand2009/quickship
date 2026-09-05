import { useCallback, useEffect, useMemo, useState } from 'react';
import MaterialCommunityIcons from '@expo/vector-icons/MaterialCommunityIcons';
import * as Print from 'expo-print';
import * as Sharing from 'expo-sharing';
import {
  ActivityIndicator,
  Alert,
  AppState,
  Image,
  KeyboardAvoidingView,
  Modal,
  Platform,
  Pressable,
  RefreshControl,
  ScrollView,
  StyleSheet,
  Text,
  TextInput,
  View,
} from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';

import * as api from '../auth/api';
import { useAuth } from '../auth/AuthContext';
import type { DashboardResponse } from '../auth/types';
import CustomersScreen from '../customers/CustomersScreen';
import type { ShippingLabelSender } from '../customers/types';
import ExpensesScreen from '../expenses/ExpensesScreen';
import OrdersScreen from '../orders/OrdersScreen';
import type { OrderListFilters } from '../orders/types';
import NotificationBridge from '../notifications/NotificationBridge';
import NotificationsScreen from '../notifications/NotificationsScreen';
import ProductSalesReportScreen from '../reports/ProductSalesReportScreen';
import StockScreen from '../stock/StockScreen';

type AppTab = 'dashboard' | 'orders' | 'expenses' | 'stock' | 'customers' | 'account' | 'notifications' | 'reports';
type TabIconName = keyof typeof MaterialCommunityIcons.glyphMap;

const TABS: { key: AppTab; label: string; icon: TabIconName; activeIcon: TabIconName }[] = [
  { key: 'dashboard', label: 'Home', icon: 'home-outline', activeIcon: 'home' },
  { key: 'orders', label: 'Orders', icon: 'clipboard-text-outline', activeIcon: 'clipboard-text' },
  { key: 'expenses', label: 'Expenses', icon: 'cash-minus', activeIcon: 'cash-minus' },
  { key: 'stock', label: 'Stock', icon: 'package-variant-closed', activeIcon: 'package-variant' },
  { key: 'customers', label: 'Customers', icon: 'account-group-outline', activeIcon: 'account-group' },
];

const METRIC_ICONS: Record<string, TabIconName> = {
  total_orders: 'clipboard-list-outline',
  waiting_orders: 'timer-sand',
  pending_orders: 'clock-outline',
  accepted_orders: 'clipboard-check-outline',
  shipped_orders: 'truck-delivery-outline',
  completed_orders: 'package-variant-closed-check',
  cancelled_orders: 'close-circle-outline',
};

const METRIC_COLORS: Record<string, { foreground: string; background: string; border: string }> = {
  total_orders: { foreground: '#14733D', background: '#ECF7EE', border: '#B9DDBF' },
  waiting_orders: { foreground: '#7A4A00', background: '#FFF9E9', border: '#EDD28B' },
  pending_orders: { foreground: '#E68200', background: '#FFF7E8', border: '#F3D28B' },
  accepted_orders: { foreground: '#14733D', background: '#ECF7EE', border: '#B9DDBF' },
  shipped_orders: { foreground: '#1769C2', background: '#EFF6FF', border: '#B6D7FF' },
  completed_orders: { foreground: '#14733D', background: '#ECF7EE', border: '#B9DDBF' },
  cancelled_orders: { foreground: '#D92D3A', background: '#FFF1F2', border: '#FFC2C7' },
};

const METRIC_ORDER_STATUSES: Record<string, string> = {
  waiting_orders: 'waiting_order',
  pending_orders: 'new_order',
  accepted_orders: 'order_accepted',
  shipped_orders: 'shipped',
  completed_orders: 'completed',
  cancelled_orders: 'order_cancelled',
};

const INDIA_POST_CUSTOMER_ID = '1828524916';

function escapeHtml(value?: string | null) {
  return String(value || '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#039;');
}

function standaloneShippingLabelHtml(
  customer: { name: string; phone: string; address: string },
  sender: ShippingLabelSender | null,
) {
  const senderName = sender?.name || 'Mathukai Organic';
  const senderPhone = sender?.phone || '9940464659';
  const senderAddress = sender?.address || 'Mathukai Organic';

  return `<!DOCTYPE html>
<html>
  <head>
    <meta charset="utf-8" />
    <style>
      @page { size: 4in 6in; margin: 0; }
      body { margin: 0; padding: 0; font-family: Arial, sans-serif; color: #111; }
      .label { box-sizing: border-box; width: 4in; height: 6in; padding: 0.2in; border: 2px solid #111; }
      .brand { font-size: 14px; font-weight: 800; letter-spacing: 0.8px; }
      .title { margin-top: 8px; font-size: 23px; font-weight: 900; }
      .customer-id { margin-top: 7px; font-size: 12px; font-weight: 800; }
      .rule { margin: 13px 0; border-top: 2px solid #111; }
      .box { border: 1.6px solid #111; padding: 12px; margin-bottom: 13px; }
      .box-title { font-size: 9px; font-weight: 900; letter-spacing: 0.6px; margin-bottom: 10px; }
      .name { font-size: 17px; font-weight: 900; margin-bottom: 8px; }
      .address { font-size: 12px; line-height: 1.35; white-space: pre-line; }
      .phone { margin-top: 8px; font-size: 12px; font-weight: 900; }
    </style>
  </head>
  <body>
    <div class="label">
      <div class="brand">MATHUKAI ORGANIC</div>
      <div class="title">SHIPPING LABEL</div>
      <div class="customer-id">India Post Customer ID: ${INDIA_POST_CUSTOMER_ID}</div>
      <div class="rule"></div>
      <div class="box">
        <div class="box-title">TO</div>
        <div class="name">${escapeHtml(customer.name)}</div>
        <div class="address">${escapeHtml(customer.address)}</div>
        <div class="phone">Phone: ${escapeHtml(customer.phone)}</div>
      </div>
      <div class="box">
        <div class="box-title">FROM</div>
        <div class="name">${escapeHtml(senderName)}</div>
        <div class="address">${escapeHtml(senderAddress)}</div>
        <div class="phone">Phone: ${escapeHtml(senderPhone)}</div>
      </div>
    </div>
  </body>
</html>`;
}

function formatUpdatedAt(value?: string) {
  if (!value) return '';
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return '';
  return parsed.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
}

function formatMetricValue(value: number | string) {
  return String(value).replace(/^â‚¹\s*/, '₹');
}

function greetingForCurrentTime() {
  const hour = new Date().getHours();
  if (hour >= 5 && hour < 12) return 'Good morning';
  if (hour >= 12 && hour < 17) return 'Good afternoon';
  if (hour >= 17 && hour < 22) return 'Good evening';
  return 'Hello';
}

function monthKey(date: Date) {
  return `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, '0')}`;
}

function dashboardMonthOptions(count = 12) {
  const current = new Date();
  return Array.from({ length: count }, (_, index) => {
    const date = new Date(current.getFullYear(), current.getMonth() - index, 1);
    return {
      key: monthKey(date),
      label: date.toLocaleDateString([], { month: 'long', year: 'numeric' }),
    };
  });
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

function destinationWithOrderStatus(destination: string, status: string) {
  const [path, query = ''] = destination.split('?', 2);
  const remainingParameters = query
    .split('&')
    .filter(Boolean)
    .filter((parameter) => !parameter.startsWith('status='));
  return `${path}?status=${encodeURIComponent(status)}${remainingParameters.length ? `&${remainingParameters.join('&')}` : ''}`;
}

function DashboardScreen({ onNavigate, onOpenProductReport }: { onNavigate: (destination: string) => void; onOpenProductReport: (month: string) => void }) {
  const { auth, runAuthenticated } = useAuth();
  const monthOptions = useMemo(() => dashboardMonthOptions(), []);
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
      if (!silent) setError(reason instanceof api.ApiError ? reason.message : 'Dashboard could not be loaded.');
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
  const totalOrdersMetric = dashboard.data.metrics.find((metric) => metric.key === 'total_orders');
  const newOrdersMetric = dashboard.data.metrics.find((metric) => metric.key === 'pending_orders');
  const pipelineMetrics = dashboard.data.metrics.filter((metric) => (
    metric.key === 'accepted_orders'
    || metric.key === 'shipped_orders'
    || metric.key === 'completed_orders'
    || metric.key === 'cancelled_orders'
  ));
  const firstName = (auth?.session.user.display_name || '').trim().split(/\s+/)[0];
  const greeting = greetingForCurrentTime();
  const todayLabel = new Date().toLocaleDateString([], { weekday: 'long', day: 'numeric', month: 'long' });

  const selectedMonthLabel = monthOptions.find((option) => option.key === selectedMonth)?.label
    || dashboard.meta.period?.label
    || selectedMonth;

  return (
    <>
      <ScrollView
        contentContainerStyle={styles.scrollContent}
        refreshControl={<RefreshControl refreshing={refreshing} onRefresh={() => void loadDashboard(true)} colors={['#0B5D3B']} tintColor="#0B5D3B" />}
      >
      {error ? <View style={styles.warning}><Text style={styles.warningText}>{error} Showing the last loaded data.</Text></View> : null}

      <View style={styles.dashboardIntro}>
        <Text style={styles.dashboardGreeting}>{greeting}{firstName ? `, ${firstName}` : ''}</Text>
        <View style={styles.dashboardMetaRow}>
          <Text style={styles.dashboardDate}>{todayLabel}</Text>
          <Text style={styles.updatedText}>Updated {formatUpdatedAt(dashboard.meta.server_time) || 'recently'}</Text>
        </View>
      </View>

      <View style={styles.periodRow}>
        <Text style={styles.currentMonthText}>Viewing month</Text>
        <Pressable
          accessibilityRole="button"
          accessibilityLabel={`Select dashboard month. Currently ${selectedMonthLabel}`}
          onPress={() => setMonthPickerVisible(true)}
          style={({ pressed }) => [styles.periodPicker, pressed && styles.pressed]}
        >
          <Text style={styles.periodPickerText}>{selectedMonthLabel}</Text>
          <MaterialCommunityIcons color="#52665E" name="chevron-down" size={20} />
        </Pressable>
      </View>

      <Text style={[styles.sectionTitle, styles.monthHeading]}>Monthly summary</Text>
      <View style={styles.performanceCard}>
        {[...financeMetrics, ...(totalOrdersMetric ? [totalOrdersMetric] : [])].map((metric, index) => (
          <Pressable
            key={metric.key}
            onPress={() => onNavigate(metric.destination)}
            style={({ pressed }) => [styles.performanceMetric, index > 0 && styles.performanceMetricBorder, pressed && styles.pressed]}
          >
            <View style={styles.performanceIcon}>
              <MaterialCommunityIcons
                color="#14733D"
                name={metric.key === 'total_sales' ? 'finance' : metric.key === 'total_profit' ? 'currency-inr' : 'clipboard-list-outline'}
                size={22}
              />
            </View>
            <Text style={styles.performanceLabel}>{metric.key === 'total_profit' ? 'Profit' : metric.key === 'total_orders' ? 'Orders' : 'Sales'}</Text>
            <Text numberOfLines={1} adjustsFontSizeToFit style={styles.performanceValue}>{formatMetricValue(metric.value)}</Text>
          </Pressable>
        ))}
      </View>
      <Pressable
        accessibilityRole="button"
        onPress={() => onOpenProductReport(selectedMonth)}
        style={({ pressed }) => [styles.reportLinkCard, pressed && styles.pressed]}
      >
        <View style={styles.reportLinkIcon}>
          <MaterialCommunityIcons color="#0B5D3B" name="chart-box-outline" size={24} />
        </View>
        <View style={styles.reportLinkCopy}>
          <Text style={styles.reportLinkTitle}>Product sales report</Text>
          <Text style={styles.reportLinkText}>Qty, sales and profit product-wise</Text>
        </View>
        <MaterialCommunityIcons color="#52665E" name="chevron-right" size={23} />
      </Pressable>

      <Text style={[styles.sectionTitle, styles.pipelineHeading]}>Order pipeline</Text>
      <View style={styles.metricGrid}>
        {pipelineMetrics.map((metric) => {
          const colors = METRIC_COLORS[metric.key] || METRIC_COLORS.total_orders;
          return (
            <Pressable
              accessibilityRole="button"
              key={metric.key}
              onPress={() => onNavigate(
                METRIC_ORDER_STATUSES[metric.key]
                  ? destinationWithOrderStatus(metric.destination, METRIC_ORDER_STATUSES[metric.key])
                  : metric.destination,
              )}
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

      <View style={styles.attentionCard}>
        <Text style={styles.attentionHeading}>Needs your attention</Text>
        {newOrdersMetric ? (
          <Pressable
            onPress={() => onNavigate(destinationWithOrderStatus(newOrdersMetric.destination, 'new_order'))}
            style={({ pressed }) => [styles.attentionRow, pressed && styles.pressed]}
          >
            <View style={[styles.attentionIcon, styles.newOrderAttentionIcon]}>
              <MaterialCommunityIcons color="#E68200" name="clock-outline" size={24} />
            </View>
            <View style={styles.attentionCopy}>
              <Text style={styles.attentionLabel}>New orders</Text>
            </View>
            <Text style={styles.attentionValue}>{formatMetricValue(newOrdersMetric.value)}</Text>
            <MaterialCommunityIcons color="#52665E" name="chevron-right" size={23} />
          </Pressable>
        ) : null}
        {dashboard.data.alerts.map((alert) => (
          <Pressable
            key={alert.id}
            onPress={() => onNavigate(alert.destination)}
            style={({ pressed }) => [styles.attentionRow, styles.attentionRowDivider, pressed && styles.pressed]}
          >
            <View style={styles.attentionIcon}>
              <MaterialCommunityIcons color="#D98200" name="alert-outline" size={24} />
            </View>
            <View style={styles.attentionCopy}>
              <Text style={styles.attentionLabel}>{alert.title}</Text>
              <Text numberOfLines={1} style={styles.attentionHint}>{alert.message}</Text>
            </View>
            <MaterialCommunityIcons color="#52665E" name="chevron-right" size={23} />
          </Pressable>
        ))}
      </View>
      </ScrollView>

      <Modal
        animationType="fade"
        onRequestClose={() => setMonthPickerVisible(false)}
        transparent
        visible={monthPickerVisible}
      >
        <View style={styles.monthModalBackdrop}>
          <Pressable
            accessibilityLabel="Close month selection"
            onPress={() => setMonthPickerVisible(false)}
            style={styles.monthModalDismissArea}
          />
          <View style={styles.monthModalCard}>
            <View style={styles.monthModalHeader}>
              <Text style={styles.monthModalTitle}>Select month</Text>
              <Pressable
                accessibilityLabel="Close month selection"
                onPress={() => setMonthPickerVisible(false)}
                style={styles.monthModalClose}
              >
                <MaterialCommunityIcons color="#40564D" name="close" size={22} />
              </Pressable>
            </View>
            <ScrollView>
              {monthOptions.map((option) => {
                const selected = option.key === selectedMonth;
                return (
                  <Pressable
                    accessibilityRole="button"
                    key={option.key}
                    onPress={() => {
                      setSelectedMonth(option.key);
                      setMonthPickerVisible(false);
                    }}
                    style={({ pressed }) => [
                      styles.monthOption,
                      selected && styles.monthOptionSelected,
                      pressed && styles.pressed,
                    ]}
                  >
                    <Text style={[styles.monthOptionText, selected && styles.monthOptionTextSelected]}>{option.label}</Text>
                    {selected ? <MaterialCommunityIcons color="#0B5D3B" name="check" size={21} /> : null}
                  </Pressable>
                );
              })}
            </ScrollView>
          </View>
        </View>
      </Modal>
    </>
  );
}

function AccountScreen() {
  const { auth, runAuthenticated, signOut } = useAuth();
  const [busy, setBusy] = useState(false);
  const [labelVisible, setLabelVisible] = useState(false);
  const [labelName, setLabelName] = useState('');
  const [labelPhone, setLabelPhone] = useState('');
  const [labelAddress, setLabelAddress] = useState('');
  const [labelBusy, setLabelBusy] = useState(false);
  const [labelSender, setLabelSender] = useState<ShippingLabelSender | null>(null);
  if (!auth?.session.active_tenant) return null;

  const labelReady = labelName.trim().length > 1 && labelPhone.replace(/\D/g, '').length >= 10 && labelAddress.trim().length > 5;

  const signOutNow = async () => {
    setBusy(true);
    await signOut();
  };

  const openLabelTool = async () => {
    setLabelName('');
    setLabelPhone('');
    setLabelAddress('');
    setLabelVisible(true);
    try {
      const response = await runAuthenticated((token) => api.shippingLabelSender(token));
      setLabelSender(response.data);
    } catch {
      setLabelSender(null);
    }
  };

  const createLabelPdf = async () => {
    if (!labelReady || labelBusy) return;
    setLabelBusy(true);
    try {
      const canShare = await Sharing.isAvailableAsync();
      if (!canShare) {
        Alert.alert('Sharing unavailable', 'PDF sharing is not available on this device.');
        return;
      }
      const pdf = await Print.printToFileAsync({
        html: standaloneShippingLabelHtml(
          { name: labelName.trim(), phone: labelPhone.trim(), address: labelAddress.trim() },
          labelSender,
        ),
        width: 288,
        height: 432,
      });
      await Sharing.shareAsync(pdf.uri, {
        dialogTitle: 'Share shipping label PDF',
        mimeType: 'application/pdf',
        UTI: 'com.adobe.pdf',
      });
      setLabelVisible(false);
    } catch {
      Alert.alert('Label not created', 'Please check the customer details and try again.');
    } finally {
      setLabelBusy(false);
    }
  };

  return (
    <>
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

        <Text style={styles.accountSectionTitle}>Tools</Text>
        <Pressable onPress={() => void openLabelTool()} style={({ pressed }) => [styles.accountToolCard, pressed && styles.pressed]}>
          <View style={styles.accountToolIcon}>
            <MaterialCommunityIcons color="#0B5D3B" name="file-pdf-box" size={27} />
          </View>
          <View style={styles.accountToolCopy}>
            <Text style={styles.accountToolTitle}>Shipping Label PDF</Text>
            <Text style={styles.accountToolText}>Paste customer address and create a 4×6 label PDF.</Text>
          </View>
          <MaterialCommunityIcons color="#587066" name="chevron-right" size={25} />
        </Pressable>

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
          <View style={styles.appMark}>
            <Image accessibilityLabel="Mathukai Organic logo" resizeMode="contain" source={require('../../assets/images/mathukai-organic-logo-transparent.png')} style={styles.appMarkImage} />
          </View>
          <View style={styles.appInfoCopy}>
            <Text style={styles.appInfoName}>Mathukai Organic</Text>
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

      <Modal animationType="slide" onRequestClose={() => setLabelVisible(false)} transparent visible={labelVisible}>
        <KeyboardAvoidingView behavior={Platform.OS === 'ios' ? 'padding' : undefined} style={styles.modalKeyboardView}>
          <View style={styles.modalBackdrop}>
            <View style={styles.labelFormSheet}>
              <View style={styles.labelHeader}>
                <View>
                  <Text style={styles.labelTitle}>Shipping Label PDF</Text>
                  <Text style={styles.labelSubtitle}>Paste the customer details received offline.</Text>
                </View>
                <Pressable onPress={() => setLabelVisible(false)} style={styles.labelCloseButton}>
                  <MaterialCommunityIcons color="#587066" name="close" size={22} />
                </Pressable>
              </View>
              <ScrollView keyboardShouldPersistTaps="handled">
                <Text style={styles.labelInputLabel}>Customer name</Text>
                <TextInput
                  autoCapitalize="words"
                  onChangeText={setLabelName}
                  placeholder="Customer name"
                  placeholderTextColor="#92A29B"
                  style={styles.labelInput}
                  value={labelName}
                />
                <Text style={styles.labelInputLabel}>Mobile number</Text>
                <TextInput
                  keyboardType="phone-pad"
                  onChangeText={setLabelPhone}
                  placeholder="Mobile number"
                  placeholderTextColor="#92A29B"
                  style={styles.labelInput}
                  value={labelPhone}
                />
                <Text style={styles.labelInputLabel}>Paste customer address</Text>
                <TextInput
                  multiline
                  onChangeText={setLabelAddress}
                  placeholder="Paste full delivery address"
                  placeholderTextColor="#92A29B"
                  style={[styles.labelInput, styles.labelTextarea]}
                  textAlignVertical="top"
                  value={labelAddress}
                />
                <Pressable
                  disabled={!labelReady || labelBusy}
                  onPress={() => void createLabelPdf()}
                  style={({ pressed }) => [styles.labelCreateButton, (!labelReady || labelBusy) && styles.disabledButton, pressed && styles.pressed]}
                >
                  {labelBusy ? <ActivityIndicator color="#FFFFFF" /> : (
                    <>
                      <MaterialCommunityIcons color="#FFFFFF" name="file-pdf-box" size={20} />
                      <Text style={styles.labelCreateText}>Create PDF</Text>
                    </>
                  )}
                </Pressable>
              </ScrollView>
            </View>
          </View>
        </KeyboardAvoidingView>
      </Modal>
    </>
  );
}

export default function OperationsApp() {
  const { auth, runAuthenticated } = useAuth();
  const [activeTab, setActiveTab] = useState<AppTab>('dashboard');
  const [ordersInitialFilters, setOrdersInitialFilters] = useState<OrderListFilters>({});
  const [ordersInitialOrderId, setOrdersInitialOrderId] = useState<number | null>(null);
  const [ordersScreenKey, setOrdersScreenKey] = useState(0);
  const [liveRefreshKey, setLiveRefreshKey] = useState(0);
  const [unreadNotificationCount, setUnreadNotificationCount] = useState(0);
  const [openingOrders, setOpeningOrders] = useState(false);
  const [reportInitialMonth, setReportInitialMonth] = useState<string | undefined>(undefined);

  const refreshUnreadCount = useCallback(async () => {
    if (!auth?.session.active_tenant) return;
    try {
      const response = await runAuthenticated((token) => api.notifications(token, { unread_only: true, page_size: 1 }));
      setUnreadNotificationCount(response.meta.unread_count || 0);
    } catch {
      // The rest of the app remains usable when notification summary refresh fails.
    }
  }, [auth?.session.active_tenant, runAuthenticated]);

  const handleNotificationReceived = useCallback(() => {
    setLiveRefreshKey((current) => current + 1);
    void refreshUnreadCount();
  }, [refreshUnreadCount]);

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

  const openProductReport = useCallback((month: string) => {
    setReportInitialMonth(month);
    setActiveTab('reports');
  }, []);

  const openTab = useCallback(async (tab: AppTab) => {
    if (tab !== 'orders') {
      setActiveTab(tab);
      return;
    }
    if (activeTab === 'orders' || openingOrders) return;

    setOpeningOrders(true);
    let preferredStatus = '';
    try {
      const waitingOrders = await runAuthenticated((token) => api.orders(token, { status: 'waiting_order' }));
      if (waitingOrders.data.length > 0) {
        preferredStatus = 'waiting_order';
      } else {
        const newOrders = await runAuthenticated((token) => api.orders(token, { status: 'new_order' }));
        if (newOrders.data.length > 0) {
          preferredStatus = 'new_order';
        } else {
          const acceptedOrders = await runAuthenticated((token) => api.orders(token, { status: 'order_accepted' }));
          if (acceptedOrders.data.length > 0) preferredStatus = 'order_accepted';
        }
      }
    } catch {
      // All orders is the safest fallback when queue counts are unavailable.
    } finally {
      setOrdersInitialFilters(preferredStatus ? { status: preferredStatus } : {});
      setOrdersInitialOrderId(null);
      setOrdersScreenKey((current) => current + 1);
      setActiveTab('orders');
      setOpeningOrders(false);
    }
  }, [activeTab, openingOrders, runAuthenticated]);
  const title = useMemo(() => {
    if (activeTab === 'dashboard') return 'Mathukai Organic';
    if (activeTab === 'notifications') return 'Notifications';
    if (activeTab === 'reports') return 'Sales Report';
    if (activeTab === 'customers') return 'Customers';
    return TABS.find((tab) => tab.key === activeTab)?.label || '';
  }, [activeTab]);
  const userInitial = (auth?.session.user.display_name || 'A').trim().slice(0, 1).toUpperCase();

  return (
    <SafeAreaView style={styles.app}>
      <View style={styles.header}>
        <View style={styles.headerBrand}>
          <View style={styles.headerMark}>
            <Image accessibilityLabel="Mathukai Organic logo" resizeMode="contain" source={require('../../assets/images/mathukai-organic-logo-transparent.png')} style={styles.headerMarkImage} />
          </View>
          <View style={styles.headerCopy}>
            <Text style={styles.headerEyebrow}>{auth?.session.active_tenant?.tenant_name}</Text>
            <Text style={styles.headerTitle}>{title}</Text>
          </View>
        </View>
        <View style={styles.headerActions}>
          <Pressable
            accessibilityLabel="Open notifications"
            onPress={() => setActiveTab('notifications')}
            style={({ pressed }) => [styles.notificationButton, pressed && styles.pressed]}
          >
            <MaterialCommunityIcons color="#0B5D3B" name={unreadNotificationCount ? 'bell' : 'bell-outline'} size={24} />
            {unreadNotificationCount ? (
              <View style={styles.notificationBadge}>
                <Text style={styles.notificationBadgeText}>{unreadNotificationCount > 99 ? '99+' : unreadNotificationCount}</Text>
              </View>
            ) : null}
          </Pressable>
          <Pressable onPress={() => setActiveTab('account')} style={({ pressed }) => [styles.headerAvatar, pressed && styles.pressed]}>
            <Text style={styles.headerAvatarText}>{userInitial}</Text>
          </Pressable>
        </View>
      </View>

      <View style={styles.content}>
        <NotificationBridge onDestination={openDestination} onNotificationReceived={handleNotificationReceived} />
        {activeTab === 'dashboard' ? <DashboardScreen key={`dashboard-${liveRefreshKey}`} onNavigate={openDestination} onOpenProductReport={openProductReport} /> : null}
        {activeTab === 'orders' ? <OrdersScreen initialFilters={ordersInitialFilters} initialOrderId={ordersInitialOrderId} key={`${ordersScreenKey}-${liveRefreshKey}`} /> : null}
        {activeTab === 'expenses' ? <ExpensesScreen /> : null}
        {activeTab === 'stock' ? <StockScreen /> : null}
        {activeTab === 'customers' ? <CustomersScreen /> : null}
        {activeTab === 'account' ? <AccountScreen /> : null}
        {activeTab === 'notifications' ? <NotificationsScreen onOpenDestination={openDestination} onUnreadCountChange={setUnreadNotificationCount} /> : null}
        {activeTab === 'reports' ? <ProductSalesReportScreen initialMonth={reportInitialMonth} onBack={() => setActiveTab('dashboard')} /> : null}
      </View>

      <View style={styles.tabBar}>
        {TABS.map((tab) => {
          const selected = tab.key === activeTab;
          return (
            <Pressable
              accessibilityRole="tab"
              accessibilityState={{ busy: tab.key === 'orders' && openingOrders, selected }}
              disabled={tab.key === 'orders' && openingOrders}
              key={tab.key}
              onPress={() => void openTab(tab.key)}
              style={({ pressed }) => [styles.tab, pressed && styles.pressed]}
            >
              <View style={[styles.tabIndicator, selected && styles.tabIndicatorActive]} />
              {tab.key === 'orders' && openingOrders ? (
                <ActivityIndicator color="#0B5D3B" size="small" style={styles.tabIcon} />
              ) : (
                <MaterialCommunityIcons
                  color={selected ? '#0B5D3B' : '#71867D'}
                  name={selected ? tab.activeIcon : tab.icon}
                  size={25}
                  style={styles.tabIcon}
                />
              )}
              <Text style={[styles.tabText, selected && styles.tabTextActive]}>{tab.label}</Text>
            </Pressable>
          );
        })}
      </View>
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  app: { flex: 1, backgroundColor: '#F7FAF8' },
  header: { minHeight: 76, backgroundColor: '#FFFFFF', borderBottomColor: '#DCE6E1', borderBottomWidth: 1, paddingHorizontal: 16, paddingVertical: 9, flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', shadowColor: '#17352A', shadowOffset: { width: 0, height: 2 }, shadowOpacity: 0.04, shadowRadius: 5, elevation: 2 },
  headerBrand: { flex: 1, flexDirection: 'row', alignItems: 'center' },
  headerMark: { width: 46, height: 46, borderRadius: 23, backgroundColor: '#E7F5EB', borderColor: '#B8DCC2', borderWidth: 1, alignItems: 'center', justifyContent: 'center' },
  headerMarkImage: { width: 40, height: 40 },
  headerCopy: { flex: 1, marginLeft: 10 },
  headerEyebrow: { color: '#71867D', fontSize: 9, fontWeight: '900', letterSpacing: 0.7, textTransform: 'uppercase' },
  headerTitle: { color: '#17352A', fontSize: 18, fontWeight: '900', marginTop: 2 },
  headerActions: { flexDirection: 'row', alignItems: 'center', columnGap: 8 },
  notificationButton: { width: 42, height: 42, borderRadius: 21, backgroundColor: '#F3F8F5', borderColor: '#DCE6E1', borderWidth: 1, alignItems: 'center', justifyContent: 'center' },
  notificationBadge: { position: 'absolute', top: -4, right: -5, minWidth: 20, height: 20, borderRadius: 10, backgroundColor: '#D92D20', borderColor: '#FFFFFF', borderWidth: 2, paddingHorizontal: 4, alignItems: 'center', justifyContent: 'center' },
  notificationBadgeText: { color: '#FFFFFF', fontSize: 9, fontWeight: '900' },
  headerAvatar: { width: 42, height: 42, borderRadius: 21, backgroundColor: '#E5F1E9', alignItems: 'center', justifyContent: 'center' },
  headerAvatarText: { color: '#17352A', fontSize: 16, fontWeight: '900' },
  content: { flex: 1 },
  scrollContent: { padding: 16, paddingBottom: 32 },
  center: { flex: 1, alignItems: 'center', justifyContent: 'center', padding: 30 },
  loadingText: { color: '#587066', marginTop: 14, fontWeight: '600' },
  warning: { backgroundColor: '#FFF4D8', borderColor: '#F0D08D', borderWidth: 1, borderRadius: 12, padding: 12, marginBottom: 16 },
  warningText: { color: '#7A4A00', lineHeight: 19 },
  sectionTitle: { color: '#17352A', fontSize: 20, fontWeight: '800' },
  updatedText: { color: '#71867D', fontSize: 12 },
  dashboardIntro: { marginBottom: 18 },
  dashboardGreeting: { color: '#17352A', fontSize: 25, fontWeight: '900', letterSpacing: -0.4 },
  dashboardMetaRow: { flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', marginTop: 5 },
  dashboardDate: { color: '#71867D', fontSize: 13 },
  attentionCard: { backgroundColor: '#F1F9F4', borderColor: '#BBDCC5', borderWidth: 1, borderRadius: 18, paddingHorizontal: 14, paddingTop: 14, paddingBottom: 4, marginTop: 22, marginBottom: 22, shadowColor: '#17352A', shadowOffset: { width: 0, height: 2 }, shadowOpacity: 0.05, shadowRadius: 6, elevation: 1 },
  attentionHeading: { color: '#08733F', fontSize: 17, fontWeight: '900', marginBottom: 6 },
  attentionRow: { minHeight: 64, flexDirection: 'row', alignItems: 'center' },
  attentionRowDivider: { borderTopColor: '#D7E9DD', borderTopWidth: 1 },
  attentionIcon: { width: 42, height: 42, borderRadius: 13, backgroundColor: '#FFF3D8', alignItems: 'center', justifyContent: 'center', marginRight: 11 },
  newOrderAttentionIcon: { backgroundColor: '#FFF5E7' },
  attentionCopy: { flex: 1 },
  attentionLabel: { color: '#29483D', fontSize: 14, fontWeight: '800' },
  attentionHint: { color: '#71867D', fontSize: 11, marginTop: 3 },
  attentionValue: { color: '#E68200', fontSize: 23, fontWeight: '900', marginHorizontal: 10 },
  sectionHeadingRow: { flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', marginBottom: 10 },
  performanceCard: { minHeight: 128, backgroundColor: '#FFFFFF', borderColor: '#DFE7E3', borderWidth: 1, borderRadius: 18, paddingVertical: 14, paddingHorizontal: 5, flexDirection: 'row', shadowColor: '#17352A', shadowOffset: { width: 0, height: 3 }, shadowOpacity: 0.07, shadowRadius: 7, elevation: 2 },
  performanceMetric: { flex: 1, alignItems: 'center', justifyContent: 'center', paddingHorizontal: 5 },
  performanceMetricBorder: { borderLeftColor: '#E2E8E5', borderLeftWidth: 1 },
  performanceIcon: { width: 39, height: 39, borderRadius: 20, backgroundColor: '#E7F5EB', alignItems: 'center', justifyContent: 'center' },
  performanceLabel: { color: '#64746D', fontSize: 11, fontWeight: '700', marginTop: 7 },
  performanceValue: { color: '#08733F', fontSize: 18, fontWeight: '900', marginTop: 4, maxWidth: '100%' },
  reportLinkCard: { minHeight: 68, backgroundColor: '#FFFFFF', borderColor: '#DFE7E3', borderWidth: 1, borderRadius: 16, padding: 13, marginTop: 12, flexDirection: 'row', alignItems: 'center', shadowColor: '#17352A', shadowOffset: { width: 0, height: 2 }, shadowOpacity: 0.05, shadowRadius: 5, elevation: 1 },
  reportLinkIcon: { width: 43, height: 43, borderRadius: 13, backgroundColor: '#EAF6EF', alignItems: 'center', justifyContent: 'center', marginRight: 11 },
  reportLinkCopy: { flex: 1 },
  reportLinkTitle: { color: '#17352A', fontSize: 15, fontWeight: '900' },
  reportLinkText: { color: '#71867D', fontSize: 12, marginTop: 3, fontWeight: '700' },
  monthHeading: { marginBottom: 10 },
  pipelineHeading: { marginTop: 22, marginBottom: 10 },
  periodRow: { minHeight: 42, flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', marginBottom: 12 },
  currentMonthText: { color: '#587066', fontSize: 12, fontWeight: '700' },
  periodPicker: { minHeight: 39, borderColor: '#C9D2CD', borderWidth: 1, borderRadius: 11, paddingHorizontal: 12, flexDirection: 'row', alignItems: 'center', columnGap: 5, backgroundColor: '#FFFFFF' },
  periodPickerText: { color: '#223B31', fontSize: 14, fontWeight: '700' },
  monthModalBackdrop: { flex: 1, backgroundColor: 'rgba(15, 35, 28, 0.52)', justifyContent: 'center', padding: 26 },
  monthModalDismissArea: { position: 'absolute', inset: 0 },
  monthModalCard: { maxHeight: '72%', backgroundColor: '#FFFFFF', borderRadius: 20, padding: 18 },
  monthModalHeader: { flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', marginBottom: 10 },
  monthModalTitle: { color: '#17352A', fontSize: 20, fontWeight: '900' },
  monthModalClose: { width: 40, height: 40, borderRadius: 20, backgroundColor: '#F1F5F3', alignItems: 'center', justifyContent: 'center' },
  monthOption: { minHeight: 50, borderBottomColor: '#E7ECEA', borderBottomWidth: 1, borderRadius: 11, paddingHorizontal: 12, flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between' },
  monthOptionSelected: { backgroundColor: '#EAF6EF' },
  monthOptionText: { color: '#40564D', fontSize: 15, fontWeight: '700' },
  monthOptionTextSelected: { color: '#0B5D3B', fontWeight: '900' },
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
  profileCard: { minHeight: 118, backgroundColor: '#FFFFFF', borderColor: '#DCE6E1', borderWidth: 1, borderRadius: 20, padding: 18, flexDirection: 'row', alignItems: 'center', shadowColor: '#17352A', shadowOffset: { width: 0, height: 3 }, shadowOpacity: 0.07, shadowRadius: 8, elevation: 2 },
  avatar: { width: 64, height: 64, borderRadius: 32, backgroundColor: '#E7F5EB', alignItems: 'center', justifyContent: 'center' },
  avatarText: { color: '#08733F', fontSize: 27, fontWeight: '900' },
  onlineBadge: { position: 'absolute', right: 1, bottom: 2, width: 15, height: 15, borderRadius: 8, backgroundColor: '#48B77B', borderColor: '#FFFFFF', borderWidth: 3 },
  profileCopy: { flex: 1, marginLeft: 15 },
  profileName: { color: '#17352A', fontSize: 20, fontWeight: '900' },
  profileUsername: { color: '#71867D', marginTop: 3 },
  roleBadge: { alignSelf: 'flex-start', backgroundColor: '#E8F5EB', borderRadius: 13, paddingHorizontal: 9, paddingVertical: 4, flexDirection: 'row', alignItems: 'center', columnGap: 5, marginTop: 9 },
  roleBadgeText: { color: '#14733D', fontSize: 10, fontWeight: '900' },
  accountSectionTitle: { color: '#17352A', fontSize: 16, fontWeight: '900', marginTop: 22, marginBottom: 10 },
  accountToolCard: { minHeight: 82, backgroundColor: '#FFFFFF', borderColor: '#D6E5DD', borderWidth: 1, borderRadius: 18, padding: 14, flexDirection: 'row', alignItems: 'center', shadowColor: '#17352A', shadowOffset: { width: 0, height: 2 }, shadowOpacity: 0.05, shadowRadius: 7, elevation: 1 },
  accountToolIcon: { width: 50, height: 50, borderRadius: 16, backgroundColor: '#E4F3EB', alignItems: 'center', justifyContent: 'center' },
  accountToolCopy: { flex: 1, marginLeft: 12, paddingRight: 8 },
  accountToolTitle: { color: '#17352A', fontSize: 15, fontWeight: '900' },
  accountToolText: { color: '#71867D', fontSize: 12, lineHeight: 17, marginTop: 4 },
  detailCard: { backgroundColor: '#FFFFFF', borderColor: '#DFE5E2', borderWidth: 1, borderRadius: 17, padding: 16 },
  accountDetailRow: { flexDirection: 'row', alignItems: 'center' },
  accountDetailIcon: { width: 42, height: 42, borderRadius: 12, backgroundColor: '#ECF7EE', alignItems: 'center', justifyContent: 'center' },
  accountDetailCopy: { flex: 1, marginLeft: 12 },
  detailLabel: { color: '#71867D', fontSize: 12, fontWeight: '700', textTransform: 'uppercase', letterSpacing: 0.7 },
  detailValue: { color: '#17352A', fontSize: 16, fontWeight: '800', marginTop: 5 },
  divider: { height: 1, backgroundColor: '#E4EAE7', marginVertical: 16 },
  appInfoCard: { minHeight: 78, backgroundColor: '#FFFFFF', borderColor: '#DFE5E2', borderWidth: 1, borderRadius: 17, padding: 13, flexDirection: 'row', alignItems: 'center' },
  appMark: { width: 52, height: 52, borderRadius: 14, backgroundColor: '#FFFFFF', borderColor: '#DCE6E1', borderWidth: 1, alignItems: 'center', justifyContent: 'center' },
  appMarkImage: { width: 46, height: 46 },
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
  modalKeyboardView: { flex: 1 },
  modalBackdrop: { flex: 1, justifyContent: 'flex-end', backgroundColor: 'rgba(15, 35, 28, 0.46)' },
  labelFormSheet: { maxHeight: '90%', backgroundColor: '#FFFFFF', borderTopLeftRadius: 24, borderTopRightRadius: 24, padding: 18, paddingBottom: 28 },
  labelHeader: { flexDirection: 'row', alignItems: 'flex-start', justifyContent: 'space-between', columnGap: 14, marginBottom: 14 },
  labelTitle: { color: '#17352A', fontSize: 22, fontWeight: '900' },
  labelSubtitle: { color: '#71867D', fontSize: 12, lineHeight: 18, marginTop: 3 },
  labelCloseButton: { width: 40, height: 40, borderRadius: 20, backgroundColor: '#F3F8F5', alignItems: 'center', justifyContent: 'center' },
  labelInputLabel: { color: '#40564D', fontSize: 12, fontWeight: '900', marginBottom: 6 },
  labelInput: { minHeight: 50, borderColor: '#CAD7D1', borderWidth: 1, borderRadius: 13, color: '#17352A', fontSize: 15, paddingHorizontal: 13, marginBottom: 13, backgroundColor: '#FFFFFF' },
  labelTextarea: { minHeight: 130, paddingTop: 12, lineHeight: 20 },
  labelCreateButton: { minHeight: 52, borderRadius: 15, backgroundColor: '#0B5D3B', alignItems: 'center', justifyContent: 'center', flexDirection: 'row', columnGap: 8, marginTop: 4 },
  labelCreateText: { color: '#FFFFFF', fontSize: 16, fontWeight: '900' },
  disabledButton: { opacity: 0.45 },
  tabBar: { minHeight: 76, backgroundColor: '#FFFFFF', borderTopColor: '#DCE5E1', borderTopWidth: 1, flexDirection: 'row', paddingHorizontal: 6, shadowColor: '#17352A', shadowOffset: { width: 0, height: -2 }, shadowOpacity: 0.06, shadowRadius: 6, elevation: 5 },
  tab: { flex: 1, alignItems: 'center', justifyContent: 'center' },
  tabIndicator: { width: 22, height: 3, borderRadius: 2, backgroundColor: 'transparent', marginBottom: 4 },
  tabIndicatorActive: { backgroundColor: '#0B5D3B' },
  tabIcon: { marginBottom: 2 },
  tabText: { color: '#71867D', fontSize: 12, fontWeight: '700' },
  tabTextActive: { color: '#0B5D3B', fontWeight: '900' },
  pressed: { opacity: 0.65 },
});
