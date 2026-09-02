import { useCallback, useEffect, useMemo, useState } from 'react';
import MaterialCommunityIcons from '@expo/vector-icons/MaterialCommunityIcons';
import {
  ActivityIndicator,
  FlatList,
  Linking,
  Pressable,
  RefreshControl,
  ScrollView,
  StyleSheet,
  Text,
  TextInput,
  View,
} from 'react-native';

import * as api from '../auth/api';
import { useAuth } from '../auth/AuthContext';
import type { CustomerSummary } from './types';
import type { Money, OrderDetail, OrderSummary } from '../orders/types';

function money(value: Money | null | undefined) {
  if (!value) return '₹ 0.00';
  return `${value.currency === 'INR' ? '₹' : value.currency} ${value.amount}`;
}

function dateLabel(value: string | null | undefined) {
  if (!value) return 'No date';
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return 'No date';
  return parsed.toLocaleDateString([], { day: '2-digit', month: 'short', year: 'numeric' });
}

function normalizePhone(value: string | null | undefined) {
  const digits = String(value || '').replace(/\D/g, '');
  if (!digits) return '';
  return digits.length > 10 ? digits.slice(-10) : digits;
}

function CustomerCard({ customer, onPress }: { customer: CustomerSummary; onPress: () => void }) {
  return (
    <Pressable onPress={onPress} style={({ pressed }) => [styles.customerCard, pressed && styles.pressed]}>
      <View style={styles.customerAvatar}>
        <Text style={styles.customerAvatarText}>{customer.name.slice(0, 1).toUpperCase()}</Text>
      </View>
      <View style={styles.customerCopy}>
        <Text numberOfLines={1} style={styles.customerName}>{customer.name}</Text>
        <Text numberOfLines={1} style={styles.customerMeta}>
          {customer.phone || customer.email || customer.address || 'Contact not available'}
        </Text>
        <View style={styles.customerStatsRow}>
          <Text style={styles.customerStat}>{customer.order_count} order{customer.order_count === 1 ? '' : 's'}</Text>
          <Text style={styles.customerStatDot}>•</Text>
          <Text style={styles.customerStat}>{money(customer.total_spent)}</Text>
        </View>
      </View>
      <View style={styles.customerRight}>
        <Text style={styles.lastOrderLabel}>Last order</Text>
        <Text style={styles.lastOrderDate}>{dateLabel(customer.last_order_at)}</Text>
        <MaterialCommunityIcons color="#52665E" name="chevron-right" size={23} />
      </View>
    </Pressable>
  );
}

function OrderHistoryCard({ order, onPress }: { order: OrderSummary; onPress: () => void }) {
  const attention = order.attention_required;
  return (
    <Pressable onPress={onPress} style={({ pressed }) => [styles.orderCard, pressed && styles.pressed]}>
      <View style={styles.orderIcon}>
        <MaterialCommunityIcons color={attention ? '#B77900' : '#0B5D3B'} name={attention ? 'alert-circle-outline' : 'receipt-text-outline'} size={24} />
      </View>
      <View style={styles.orderCopy}>
        <View style={styles.orderTopRow}>
          <Text style={styles.orderReference}>Order {order.reference}</Text>
          <Text style={styles.orderTotal}>{money(order.total)}</Text>
        </View>
        <Text style={styles.orderMeta}>{dateLabel(order.order_date)} · {order.item_count} item{order.item_count === 1 ? '' : 's'}</Text>
        <View style={styles.pillRow}>
          <Text style={[styles.statusPill, attention && styles.attentionPill]}>{order.status.label}</Text>
          <Text style={styles.paymentPill}>Payment: {order.payment_state.label}</Text>
        </View>
      </View>
      <MaterialCommunityIcons color="#52665E" name="chevron-right" size={22} />
    </Pressable>
  );
}

function DetailRow({ label, value }: { label: string; value: string | null | undefined }) {
  if (!value) return null;
  return (
    <View style={styles.detailRow}>
      <Text style={styles.detailLabel}>{label}</Text>
      <Text selectable style={styles.detailValue}>{value}</Text>
    </View>
  );
}

function OrderDetailView({ order, onBack }: { order: OrderDetail; onBack: () => void }) {
  return (
    <ScrollView contentContainerStyle={styles.scrollContent}>
      <Pressable onPress={onBack} style={styles.backButton}>
        <MaterialCommunityIcons color="#0B5D3B" name="arrow-left" size={21} />
        <Text style={styles.backText}>Back to customer</Text>
      </Pressable>

      <View style={styles.heroCard}>
        <View style={styles.heroTop}>
          <View>
            <Text style={styles.heroTitle}>Order {order.reference}</Text>
            <Text style={styles.heroSubtitle}>{dateLabel(order.order_date)} · {order.status.label}</Text>
          </View>
          <Text style={styles.heroAmount}>{money(order.total)}</Text>
        </View>
        <Text style={styles.heroPayment}>Payment: {order.payment_state.label}</Text>
      </View>

      <Text style={styles.sectionTitle}>Customer details</Text>
      <View style={styles.sectionCard}>
        <DetailRow label="Name" value={order.customer.name} />
        <DetailRow label="Phone" value={order.customer.phone} />
        <DetailRow label="Email" value={order.customer.email} />
        <DetailRow label="Address" value={order.customer.delivery_address} />
      </View>

      <Text style={styles.sectionTitle}>Items</Text>
      <View style={styles.sectionCard}>
        {order.items.length ? order.items.map((item, index) => (
          <View key={`${item.sku || item.name}-${index}`} style={[styles.itemRow, index > 0 && styles.dividerTop]}>
            <View style={styles.itemCopy}>
              <Text style={styles.itemName}>{item.name}</Text>
              <Text style={styles.itemMeta}>Qty {item.quantity}{item.sku ? ` · SKU ${item.sku}` : ''}</Text>
            </View>
            <Text style={styles.itemAmount}>{money(item.total)}</Text>
          </View>
        )) : <Text style={styles.emptyText}>No item details available.</Text>}
      </View>

      <Text style={styles.sectionTitle}>Shipping</Text>
      <View style={styles.sectionCard}>
        <DetailRow label="Courier" value={order.courier_name} />
        <DetailRow label="Tracking number" value={order.tracking_number} />
        <DetailRow label="Weight" value={order.package_weight_kg ? `${order.package_weight_kg} kg` : null} />
        <DetailRow label="Shipping amount" value={money(order.shipping_total)} />
      </View>
    </ScrollView>
  );
}

function CustomerDetailScreen({ customerKey, onBack }: { customerKey: string; onBack: () => void }) {
  const { runAuthenticated } = useAuth();
  const [customer, setCustomer] = useState<CustomerSummary | null>(null);
  const [orders, setOrders] = useState<OrderSummary[]>([]);
  const [selectedOrder, setSelectedOrder] = useState<OrderDetail | null>(null);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState('');

  const load = useCallback(async (refresh = false) => {
    if (refresh) setRefreshing(true); else setLoading(true);
    setError('');
    try {
      const response = await runAuthenticated((token) => api.customerDetail(token, customerKey));
      setCustomer(response.data.customer);
      setOrders(response.data.orders);
    } catch (reason) {
      setError(reason instanceof api.ApiError ? reason.message : 'Customer details could not be loaded.');
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }, [customerKey, runAuthenticated]);

  useEffect(() => { void load(); }, [load]);

  const openOrder = async (orderId: number) => {
    setError('');
    try {
      const response = await runAuthenticated((token) => api.customerOrderDetail(token, customerKey, orderId));
      setSelectedOrder(response.data);
    } catch (reason) {
      setError(reason instanceof api.ApiError ? reason.message : 'Order details could not be loaded.');
    }
  };

  if (selectedOrder) return <OrderDetailView order={selectedOrder} onBack={() => setSelectedOrder(null)} />;
  if (loading && !customer) return <View style={styles.center}><ActivityIndicator size="large" color="#0B5D3B" /><Text style={styles.loadingText}>Loading customer...</Text></View>;
  if (!customer) return (
    <View style={styles.center}>
      <Text style={styles.errorTitle}>Customer unavailable</Text>
      <Text style={styles.errorMessage}>{error || 'Try again after refreshing.'}</Text>
      <Pressable onPress={onBack} style={styles.primaryButton}><Text style={styles.primaryButtonText}>Back to customers</Text></Pressable>
    </View>
  );

  const phone = normalizePhone(customer.phone);
  const openCall = () => phone ? Linking.openURL(`tel:+91${phone}`).catch(() => undefined) : undefined;
  const openWhatsApp = () => phone ? Linking.openURL(`https://wa.me/91${phone}`).catch(() => undefined) : undefined;

  return (
    <ScrollView
      contentContainerStyle={styles.scrollContent}
      refreshControl={<RefreshControl refreshing={refreshing} onRefresh={() => void load(true)} colors={['#0B5D3B']} tintColor="#0B5D3B" />}
    >
      <Pressable onPress={onBack} style={styles.backButton}>
        <MaterialCommunityIcons color="#0B5D3B" name="arrow-left" size={21} />
        <Text style={styles.backText}>Back to customers</Text>
      </Pressable>
      {error ? <View style={styles.warning}><Text style={styles.warningText}>{error}</Text></View> : null}

      <View style={styles.profileCard}>
        <View style={styles.profileAvatar}><Text style={styles.profileAvatarText}>{customer.name.slice(0, 1).toUpperCase()}</Text></View>
        <View style={styles.profileCopy}>
          <Text style={styles.profileName}>{customer.name}</Text>
          <Text style={styles.profileMeta}>{customer.order_count} order{customer.order_count === 1 ? '' : 's'} · {money(customer.total_spent)}</Text>
        </View>
      </View>

      <View style={styles.contactGrid}>
        <Pressable disabled={!phone} onPress={openCall} style={[styles.contactButton, !phone && styles.disabledCard]}>
          <MaterialCommunityIcons color="#0B5D3B" name="phone-outline" size={22} />
          <Text style={styles.contactButtonText}>Call</Text>
        </Pressable>
        <Pressable disabled={!phone} onPress={openWhatsApp} style={[styles.contactButton, styles.whatsAppButton, !phone && styles.disabledCard]}>
          <MaterialCommunityIcons color="#128C4A" name="whatsapp" size={22} />
          <Text style={styles.contactButtonText}>WhatsApp</Text>
        </Pressable>
      </View>

      <Text style={styles.sectionTitle}>Customer details</Text>
      <View style={styles.sectionCard}>
        <DetailRow label="Phone" value={customer.phone} />
        <DetailRow label="Email" value={customer.email} />
        <DetailRow label="Address" value={customer.address} />
        <DetailRow label="Latest order" value={customer.latest_order_reference ? `Order ${customer.latest_order_reference}` : null} />
      </View>

      <Text style={styles.sectionTitle}>Order history</Text>
      {orders.length ? orders.map((order) => (
        <OrderHistoryCard key={order.id} order={order} onPress={() => void openOrder(order.id)} />
      )) : <Text style={styles.emptyText}>No order history found.</Text>}
    </ScrollView>
  );
}

export default function CustomersScreen() {
  const { runAuthenticated } = useAuth();
  const [customers, setCustomers] = useState<CustomerSummary[]>([]);
  const [selectedCustomerKey, setSelectedCustomerKey] = useState<string | null>(null);
  const [draftSearch, setDraftSearch] = useState('');
  const [search, setSearch] = useState('');
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState('');

  const load = useCallback(async (refresh = false) => {
    if (refresh) setRefreshing(true); else setLoading(true);
    setError('');
    try {
      const response = await runAuthenticated((token) => api.customers(token, search));
      setCustomers(response.data);
    } catch (reason) {
      setError(reason instanceof api.ApiError ? reason.message : 'Customers could not be loaded.');
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }, [runAuthenticated, search]);

  useEffect(() => { void load(); }, [load]);

  const totalOrders = useMemo(() => customers.reduce((sum, customer) => sum + customer.order_count, 0), [customers]);

  if (selectedCustomerKey) return <CustomerDetailScreen customerKey={selectedCustomerKey} onBack={() => setSelectedCustomerKey(null)} />;

  const header = (
    <View>
      <View style={styles.introCard}>
        <Text style={styles.introTitle}>Customers</Text>
        <Text style={styles.introText}>Open a customer to see contact details, delivery address, and full order history.</Text>
        <View style={styles.summaryRow}>
          <View style={styles.summaryBox}>
            <Text style={styles.summaryValue}>{customers.length}</Text>
            <Text style={styles.summaryLabel}>Customers</Text>
          </View>
          <View style={styles.summaryDivider} />
          <View style={styles.summaryBox}>
            <Text style={styles.summaryValue}>{totalOrders}</Text>
            <Text style={styles.summaryLabel}>Orders shown</Text>
          </View>
        </View>
      </View>

      <View style={styles.searchRow}>
        <TextInput
          autoCapitalize="none"
          onChangeText={setDraftSearch}
          onSubmitEditing={() => setSearch(draftSearch.trim())}
          placeholder="Search name, phone, order, address"
          placeholderTextColor="#82958D"
          returnKeyType="search"
          style={styles.searchInput}
          value={draftSearch}
        />
        <Pressable onPress={() => setSearch(draftSearch.trim())} style={styles.searchButton}>
          <MaterialCommunityIcons color="#FFFFFF" name="magnify" size={23} />
        </Pressable>
      </View>
      {error && customers.length ? <View style={styles.warning}><Text style={styles.warningText}>{error}</Text></View> : null}
    </View>
  );

  if (loading && !customers.length) return <View style={styles.center}><ActivityIndicator size="large" color="#0B5D3B" /><Text style={styles.loadingText}>Loading customers...</Text></View>;
  if (error && !customers.length) return (
    <View style={styles.center}>
      <Text style={styles.errorTitle}>Customers unavailable</Text>
      <Text style={styles.errorMessage}>{error}</Text>
      <Pressable onPress={() => void load()} style={styles.primaryButton}><Text style={styles.primaryButtonText}>Try again</Text></Pressable>
    </View>
  );

  return (
    <FlatList
      contentContainerStyle={styles.listContent}
      data={customers}
      keyExtractor={(item) => item.key}
      ListEmptyComponent={<View style={styles.emptyState}><Text style={styles.emptyTitle}>No customers found</Text><Text style={styles.emptyText}>Try another search.</Text></View>}
      ListHeaderComponent={header}
      refreshControl={<RefreshControl refreshing={refreshing} onRefresh={() => void load(true)} colors={['#0B5D3B']} tintColor="#0B5D3B" />}
      renderItem={({ item }) => <CustomerCard customer={item} onPress={() => setSelectedCustomerKey(item.key)} />}
    />
  );
}

const styles = StyleSheet.create({
  center: { flex: 1, alignItems: 'center', justifyContent: 'center', padding: 30 },
  loadingText: { color: '#587066', marginTop: 14, fontWeight: '600' },
  listContent: { padding: 16, paddingBottom: 32 },
  scrollContent: { padding: 16, paddingBottom: 32 },
  pressed: { opacity: 0.72 },
  introCard: { backgroundColor: '#0B5D3B', borderRadius: 22, padding: 18, marginBottom: 14 },
  introTitle: { color: '#FFFFFF', fontSize: 25, fontWeight: '900' },
  introText: { color: '#D7EFE3', fontSize: 13, lineHeight: 19, marginTop: 6 },
  summaryRow: { backgroundColor: 'rgba(255,255,255,0.12)', borderRadius: 16, flexDirection: 'row', marginTop: 16, paddingVertical: 12 },
  summaryBox: { flex: 1, alignItems: 'center' },
  summaryDivider: { width: 1, backgroundColor: 'rgba(255,255,255,0.24)' },
  summaryValue: { color: '#FFFFFF', fontSize: 23, fontWeight: '900' },
  summaryLabel: { color: '#D7EFE3', fontSize: 11, fontWeight: '800', marginTop: 3 },
  searchRow: { flexDirection: 'row', marginBottom: 13 },
  searchInput: { flex: 1, minHeight: 50, backgroundColor: '#FFFFFF', borderColor: '#CBD9D3', borderWidth: 1, borderRadius: 14, paddingHorizontal: 15, color: '#17352A', fontSize: 15 },
  searchButton: { width: 50, height: 50, backgroundColor: '#0B5D3B', borderRadius: 14, alignItems: 'center', justifyContent: 'center', marginLeft: 8 },
  customerCard: { minHeight: 112, backgroundColor: '#FFFFFF', borderColor: '#DEE7E3', borderWidth: 1, borderRadius: 18, padding: 13, marginBottom: 11, flexDirection: 'row', alignItems: 'center', shadowColor: '#17352A', shadowOffset: { width: 0, height: 2 }, shadowOpacity: 0.05, shadowRadius: 6, elevation: 1 },
  customerAvatar: { width: 48, height: 48, borderRadius: 24, backgroundColor: '#E4F3EB', borderColor: '#B8D5C8', borderWidth: 1, alignItems: 'center', justifyContent: 'center', marginRight: 11 },
  customerAvatarText: { color: '#0B5D3B', fontSize: 19, fontWeight: '900' },
  customerCopy: { flex: 1, paddingRight: 8 },
  customerName: { color: '#17352A', fontSize: 16, fontWeight: '900' },
  customerMeta: { color: '#71867D', fontSize: 12, marginTop: 4 },
  customerStatsRow: { flexDirection: 'row', alignItems: 'center', marginTop: 9, columnGap: 6 },
  customerStat: { color: '#0B5D3B', fontSize: 12, fontWeight: '900' },
  customerStatDot: { color: '#9AABA3', fontSize: 12, fontWeight: '900' },
  customerRight: { alignItems: 'flex-end', justifyContent: 'center' },
  lastOrderLabel: { color: '#82958D', fontSize: 10, fontWeight: '800' },
  lastOrderDate: { color: '#29483D', fontSize: 11, fontWeight: '800', marginTop: 3, marginBottom: 6 },
  backButton: { minHeight: 48, backgroundColor: '#FFFFFF', borderColor: '#DCE5E1', borderWidth: 1, borderRadius: 13, paddingHorizontal: 14, flexDirection: 'row', alignItems: 'center', marginBottom: 12, columnGap: 8 },
  backText: { color: '#0B5D3B', fontSize: 15, fontWeight: '800' },
  warning: { backgroundColor: '#FFF4D8', borderColor: '#F0D08D', borderWidth: 1, borderRadius: 12, padding: 12, marginBottom: 12 },
  warningText: { color: '#7A4A00', lineHeight: 19 },
  errorTitle: { color: '#17352A', fontSize: 21, fontWeight: '800', textAlign: 'center' },
  errorMessage: { color: '#587066', lineHeight: 21, textAlign: 'center', marginTop: 8 },
  primaryButton: { backgroundColor: '#0B5D3B', minHeight: 48, borderRadius: 13, paddingHorizontal: 24, alignItems: 'center', justifyContent: 'center', marginTop: 20 },
  primaryButtonText: { color: '#FFFFFF', fontWeight: '800' },
  profileCard: { backgroundColor: '#FFFFFF', borderColor: '#DEE7E3', borderWidth: 1, borderRadius: 20, padding: 16, marginBottom: 12, flexDirection: 'row', alignItems: 'center' },
  profileAvatar: { width: 58, height: 58, borderRadius: 29, backgroundColor: '#0B5D3B', alignItems: 'center', justifyContent: 'center', marginRight: 13 },
  profileAvatarText: { color: '#FFFFFF', fontSize: 24, fontWeight: '900' },
  profileCopy: { flex: 1 },
  profileName: { color: '#17352A', fontSize: 21, fontWeight: '900' },
  profileMeta: { color: '#71867D', fontSize: 13, marginTop: 4 },
  contactGrid: { flexDirection: 'row', columnGap: 10, marginBottom: 20 },
  contactButton: { flex: 1, minHeight: 50, borderColor: '#B8D5C8', borderWidth: 1, borderRadius: 14, backgroundColor: '#F4FAF7', alignItems: 'center', justifyContent: 'center', flexDirection: 'row', columnGap: 8 },
  whatsAppButton: { backgroundColor: '#ECF9F1' },
  contactButtonText: { color: '#0B5D3B', fontSize: 14, fontWeight: '900' },
  disabledCard: { opacity: 0.45 },
  sectionTitle: { color: '#17352A', fontSize: 19, fontWeight: '900', marginBottom: 10 },
  sectionCard: { backgroundColor: '#FFFFFF', borderColor: '#E0E7E3', borderWidth: 1, borderRadius: 17, padding: 16, marginBottom: 20 },
  detailRow: { marginBottom: 13 },
  detailLabel: { color: '#71867D', fontSize: 11, fontWeight: '800', letterSpacing: 0.6, textTransform: 'uppercase' },
  detailValue: { color: '#29483D', fontSize: 15, lineHeight: 21, fontWeight: '600', marginTop: 4 },
  orderCard: { backgroundColor: '#FFFFFF', borderColor: '#E0E7E3', borderWidth: 1, borderRadius: 17, padding: 13, marginBottom: 11, flexDirection: 'row', alignItems: 'center' },
  orderIcon: { width: 44, height: 44, borderRadius: 14, backgroundColor: '#F1F9F4', alignItems: 'center', justifyContent: 'center', marginRight: 11 },
  orderCopy: { flex: 1 },
  orderTopRow: { flexDirection: 'row', justifyContent: 'space-between', columnGap: 12 },
  orderReference: { color: '#17352A', fontSize: 15, fontWeight: '900' },
  orderTotal: { color: '#17352A', fontSize: 15, fontWeight: '900' },
  orderMeta: { color: '#71867D', fontSize: 12, marginTop: 4 },
  pillRow: { flexDirection: 'row', flexWrap: 'wrap', gap: 7, marginTop: 8 },
  statusPill: { backgroundColor: '#E4F3EB', borderRadius: 12, color: '#147348', fontSize: 10, fontWeight: '900', overflow: 'hidden', paddingHorizontal: 8, paddingVertical: 4 },
  attentionPill: { backgroundColor: '#FFF4D8', color: '#9A5B00' },
  paymentPill: { backgroundColor: '#F3F8F5', borderRadius: 12, color: '#587066', fontSize: 10, fontWeight: '900', overflow: 'hidden', paddingHorizontal: 8, paddingVertical: 4 },
  heroCard: { backgroundColor: '#FFFFFF', borderColor: '#DEE7E3', borderWidth: 1, borderRadius: 18, padding: 18, marginBottom: 20 },
  heroTop: { flexDirection: 'row', justifyContent: 'space-between', columnGap: 12 },
  heroTitle: { color: '#17352A', fontSize: 21, fontWeight: '900' },
  heroSubtitle: { color: '#71867D', fontSize: 12, marginTop: 4 },
  heroAmount: { color: '#0B5D3B', fontSize: 20, fontWeight: '900' },
  heroPayment: { color: '#587066', fontSize: 13, fontWeight: '800', marginTop: 12 },
  itemRow: { flexDirection: 'row', justifyContent: 'space-between', columnGap: 12, paddingVertical: 10 },
  dividerTop: { borderTopColor: '#E7ECEA', borderTopWidth: 1 },
  itemCopy: { flex: 1 },
  itemName: { color: '#17352A', fontSize: 14, fontWeight: '900' },
  itemMeta: { color: '#71867D', fontSize: 12, marginTop: 3 },
  itemAmount: { color: '#17352A', fontSize: 14, fontWeight: '900' },
  emptyState: { alignItems: 'center', paddingVertical: 48 },
  emptyTitle: { color: '#17352A', fontSize: 20, fontWeight: '800' },
  emptyText: { color: '#71867D', lineHeight: 20, textAlign: 'center', marginTop: 7 },
});
