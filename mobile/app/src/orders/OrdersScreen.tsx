import { useCallback, useEffect, useState } from 'react';
import MaterialCommunityIcons from '@expo/vector-icons/MaterialCommunityIcons';
import {
  ActivityIndicator,
  Alert,
  FlatList,
  Linking,
  Modal,
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
import type { Money, OrderAction, OrderDetail, OrderListFilters, OrderStatusUpdate, OrderSummary } from './types';

const STATUS_FILTERS = [
  { code: '', label: 'All' },
  { code: 'new_order', label: 'New' },
  { code: 'order_accepted', label: 'Accepted' },
  { code: 'order_packed', label: 'Packed' },
  { code: 'shipped', label: 'Shipped' },
  { code: 'delivery_issue', label: 'Attention' },
  { code: 'delivered', label: 'Delivered' },
  { code: 'completed', label: 'Completed' },
  { code: 'order_cancelled', label: 'Cancelled' },
];

const CANCELLATION_REASONS = [
  { code: 'customer_request', label: 'Customer request' },
  { code: 'payment_failed', label: 'Payment failed' },
  { code: 'out_of_stock', label: 'Out of stock' },
  { code: 'address_issue', label: 'Address issue' },
  { code: 'courier_issue', label: 'Courier issue' },
  { code: 'other', label: 'Other' },
];

const ACTION_LABELS: Record<string, string> = {
  order_accepted: 'Accept order',
  shipped: 'Mark shipped',
  out_for_delivery: 'Mark out for delivery',
  delivered: 'Mark delivered',
  completed: 'Mark completed',
  order_cancelled: 'Cancel order',
};

function actionLabel(action: OrderAction) {
  if (action.code === 'mark_payment_received') return 'Mark payment received';
  return ACTION_LABELS[action.target_status || ''] || action.label;
}

function newIdempotencyKey() {
  return `android-${Date.now()}-${Math.random().toString(36).slice(2, 12)}`;
}

function money(value: Money) {
  return `${value.currency === 'INR' ? '₹' : value.currency} ${value.amount}`;
}

function dateTime(value: string | null | undefined) {
  if (!value) return 'Not available';
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return 'Not available';
  return parsed.toLocaleString([], { day: '2-digit', month: 'short', hour: '2-digit', minute: '2-digit' });
}

function StatusPill({ order }: { order: OrderSummary }) {
  return (
    <View style={[styles.statusPill, order.attention_required && styles.statusPillCritical]}>
      <Text style={[styles.statusText, order.attention_required && styles.statusTextCritical]}>{order.status.label}</Text>
    </View>
  );
}

function PaymentPill({ order }: { order: OrderSummary }) {
  const received = order.payment_state.code === 'received';
  return (
    <View style={[styles.paymentPill, received ? styles.paymentPillReceived : styles.paymentPillPending]}>
      <Text style={[styles.paymentPillText, received ? styles.paymentPillTextReceived : styles.paymentPillTextPending]}>
        Payment: {order.payment_state.label}
      </Text>
    </View>
  );
}

function normalizedContactPhone(value: string | null | undefined) {
  if (!value || /[•*xX]/.test(value)) return '';
  const digits = value.replace(/\D/g, '');
  if (digits.length === 10) return `91${digits}`;
  if (digits.length === 11 && digits.startsWith('0')) return `91${digits.slice(1)}`;
  return digits.length >= 8 ? digits : '';
}

function OrderCard({ order, onPress }: { order: OrderSummary; onPress: () => void }) {
  return (
    <Pressable onPress={onPress} style={({ pressed }) => [styles.orderCard, pressed && styles.pressed]}>
      <View style={styles.orderTopRow}>
        <View style={styles.orderReferenceWrap}>
          <Text style={styles.orderReference}>{order.reference}</Text>
          <Text style={styles.orderSource}>{order.source.label} · {dateTime(order.order_date)}</Text>
        </View>
        <StatusPill order={order} />
      </View>
      <Text style={styles.customerName}>{order.customer_display_name || 'Customer unavailable'}</Text>
      <View style={styles.orderBottomRow}>
        <View style={styles.orderSummaryCopy}>
          <View style={styles.orderMetaRow}>
            <Text style={styles.orderMeta}>{order.item_count} item{order.item_count === 1 ? '' : 's'}</Text>
            <PaymentPill order={order} />
          </View>
          {order.status.code === 'shipped' && order.tracking_number ? (
            <Text numberOfLines={1} selectable style={styles.trackingText}>Tracking: {order.tracking_number}</Text>
          ) : null}
        </View>
        <Text style={styles.orderTotal}>{money(order.total)}</Text>
      </View>
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

function OrderDetailScreen({ orderId, onBack }: { orderId: number; onBack: () => void }) {
  const { runAuthenticated } = useAuth();
  const [order, setOrder] = useState<OrderDetail | null>(null);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState('');
  const [selectedAction, setSelectedAction] = useState<OrderAction | null>(null);
  const [submittingAction, setSubmittingAction] = useState(false);
  const [customerPhone, setCustomerPhone] = useState('');
  const [courierName, setCourierName] = useState('');
  const [trackingNumber, setTrackingNumber] = useState('');
  const [shippingCost, setShippingCost] = useState('');
  const [cancellationReason, setCancellationReason] = useState('');
  const [cancellationNote, setCancellationNote] = useState('');

  const load = useCallback(async (refresh = false) => {
    if (refresh) setRefreshing(true); else setLoading(true);
    setError('');
    try {
      const response = await runAuthenticated((token) => api.orderDetail(token, orderId));
      setOrder(response.data);
    } catch (reason) {
      setError(reason instanceof api.ApiError ? reason.message : 'Order details could not be loaded.');
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }, [orderId, runAuthenticated]);

  useEffect(() => { void load(); }, [load]);

  const applyMutationResult = (updatedOrder: OrderDetail, warningMessages: string[]) => {
    setOrder(updatedOrder);
    setSelectedAction(null);
    if (warningMessages.length) {
      Alert.alert('Order updated with a warning', warningMessages.join('\n'));
    } else {
      Alert.alert('Order updated', 'The latest order details are now shown.');
    }
  };

  const handleMutationError = async (reason: unknown) => {
    if (reason instanceof api.ApiError && reason.status === 409) {
      await load(true);
      Alert.alert('Order was refreshed', 'This order changed before your action was saved. Review it and try again.');
      return;
    }
    Alert.alert('Action not completed', reason instanceof api.ApiError ? reason.message : 'Please try again.');
  };

  const submitStatusAction = async (action: OrderAction, values: Partial<OrderStatusUpdate> = {}) => {
    if (!order || !action.target_status || submittingAction) return;
    setSubmittingAction(true);
    try {
      const response = await runAuthenticated((token) => api.updateOrderStatus(
        token,
        order.id,
        { target_status: action.target_status as string, expected_version: order.version, ...values },
        newIdempotencyKey(),
      ));
      const warnings = response.data.effects
        .filter((effect) => effect.state === 'warning' && effect.message)
        .map((effect) => effect.message as string);
      applyMutationResult(response.data.order, warnings);
    } catch (reason) {
      await handleMutationError(reason);
    } finally {
      setSubmittingAction(false);
    }
  };

  const submitPaymentReceived = async () => {
    if (!order || submittingAction) return;
    setSubmittingAction(true);
    try {
      const response = await runAuthenticated((token) => api.markOrderPaymentReceived(
        token,
        order.id,
        order.version,
        newIdempotencyKey(),
      ));
      applyMutationResult(response.data.order, []);
    } catch (reason) {
      await handleMutationError(reason);
    } finally {
      setSubmittingAction(false);
    }
  };

  const openAction = (action: OrderAction) => {
    if (action.code === 'mark_payment_received') {
      Alert.alert(
        'Confirm payment',
        'Only confirm after you have received the customer payment.',
        [
          { text: 'Not yet', style: 'cancel' },
          { text: 'Payment received', onPress: () => void submitPaymentReceived() },
        ],
      );
      return;
    }
    const needsForm = action.required_fields.length > 0 || action.reason_required;
    if (needsForm) {
      setCustomerPhone(order?.customer.phone || '');
      setCourierName(order?.courier_name || '');
      setTrackingNumber(order?.tracking_number || '');
      setShippingCost(order?.shipping_cost.amount === '0.00' ? '' : order?.shipping_cost.amount || '');
      setCancellationReason('');
      setCancellationNote('');
      setSelectedAction(action);
      return;
    }
    Alert.alert(
      actionLabel(action),
      'This will update the order and connected services.',
      [
        { text: 'Cancel', style: 'cancel' },
        { text: 'Confirm', onPress: () => void submitStatusAction(action) },
      ],
    );
  };

  const submitActionForm = () => {
    if (!selectedAction) return;
    const values: Partial<OrderStatusUpdate> = {};
    if (selectedAction.required_fields.includes('customer_phone')) values.customer_phone = customerPhone.trim();
    if (selectedAction.target_status === 'shipped') {
      values.courier_name = courierName.trim();
      values.tracking_number = trackingNumber.trim().toUpperCase();
      values.shipping_base_amount = shippingCost.trim();
    }
    if (selectedAction.target_status === 'order_cancelled') {
      values.cancellation_reason = cancellationReason;
      values.cancellation_note = cancellationNote.trim();
    }
    void submitStatusAction(selectedAction, values);
  };

  if (loading && !order) return <View style={styles.center}><ActivityIndicator size="large" color="#0B5D3B" /></View>;
  if (!order) return (
    <View style={styles.center}>
      <Text style={styles.errorTitle}>Order unavailable</Text>
      <Text style={styles.errorMessage}>{error}</Text>
      <Pressable onPress={onBack} style={styles.primaryButton}><Text style={styles.primaryButtonText}>Back to orders</Text></Pressable>
    </View>
  );

  const contactPhone = normalizedContactPhone(order.customer.phone);
  const actionFormReady = selectedAction
    ? (!selectedAction.required_fields.includes('customer_phone') || customerPhone.replace(/\D/g, '').length >= 10)
      && (selectedAction.target_status !== 'shipped'
        || Boolean(courierName.trim() && /^[A-Za-z]{2}\d{9}[A-Za-z]{2}$/.test(trackingNumber.trim()) && shippingCost.trim()))
      && (selectedAction.target_status !== 'order_cancelled' || Boolean(cancellationReason))
    : false;
  const openWhatsApp = async () => {
    const customerName = order.customer.name || 'Customer';
    const message = `Hello ${customerName}, this is Mathukai regarding order ${order.reference}.`;
    try {
      await Linking.openURL(`https://wa.me/${contactPhone}?text=${encodeURIComponent(message)}`);
    } catch {
      Alert.alert('WhatsApp unavailable', 'WhatsApp could not be opened on this device.');
    }
  };
  const openDialer = async () => {
    try {
      await Linking.openURL(`tel:+${contactPhone}`);
    } catch {
      Alert.alert('Dialer unavailable', 'The phone dialer could not be opened on this device.');
    }
  };

  return (
    <ScrollView
      contentContainerStyle={styles.detailContent}
      refreshControl={<RefreshControl refreshing={refreshing} onRefresh={() => void load(true)} colors={['#0B5D3B']} tintColor="#0B5D3B" />}
    >
      <Pressable onPress={onBack} style={styles.backButton}><Text style={styles.backText}>‹ Back to orders</Text></Pressable>
      {error ? <View style={styles.warning}><Text style={styles.warningText}>{error} Showing the last loaded details.</Text></View> : null}

      <View style={styles.detailHero}>
        <View style={styles.orderTopRow}>
          <View style={styles.orderReferenceWrap}>
            <Text style={styles.detailReference}>{order.reference}</Text>
            <Text style={styles.orderSource}>{order.source.label}</Text>
          </View>
          <StatusPill order={order} />
        </View>
        <View style={styles.heroTotals}>
          <Text style={styles.heroTotal}>{money(order.total)}</Text>
          <Text style={styles.paymentText}>Payment: {order.payment_state.label}</Text>
        </View>
      </View>

      {order.allowed_actions.length ? (
        <>
          <Text style={styles.sectionTitle}>Order actions</Text>
          <View style={styles.sectionCard}>
            <Text style={styles.actionHelp}>Only actions currently allowed for this order are shown.</Text>
            <View style={styles.actionList}>
              {order.allowed_actions.map((action) => {
                const destructive = action.target_status === 'order_cancelled';
                return (
                  <Pressable
                    disabled={submittingAction}
                    key={`${action.code}-${action.target_status || 'payment'}`}
                    onPress={() => openAction(action)}
                    style={({ pressed }) => [
                      styles.actionButton,
                      destructive && styles.destructiveActionButton,
                      (pressed || submittingAction) && styles.pressed,
                    ]}
                  >
                    <MaterialCommunityIcons
                      color={destructive ? '#B42318' : '#0B5D3B'}
                      name={action.code === 'mark_payment_received' ? 'cash-check' : destructive ? 'close-circle-outline' : 'arrow-right-circle-outline'}
                      size={21}
                    />
                    <Text style={[styles.actionButtonText, destructive && styles.destructiveActionText]}>{actionLabel(action)}</Text>
                  </Pressable>
                );
              })}
            </View>
          </View>
        </>
      ) : null}

      <Text style={styles.sectionTitle}>Customer</Text>
      <View style={styles.sectionCard}>
        <DetailRow label="Name" value={order.customer.name} />
        <DetailRow label="Phone" value={order.customer.phone} />
        <DetailRow label="Email" value={order.customer.email} />
        <DetailRow label="Delivery address" value={order.customer.delivery_address} />
        {order.customer.fields_masked.length ? <Text style={styles.maskedNote}>Some customer fields are hidden for your role.</Text> : null}
        {contactPhone ? (
          <View style={styles.contactActions}>
            <Pressable
              accessibilityLabel={`Message ${order.customer.name || 'customer'} on WhatsApp`}
              onPress={() => void openWhatsApp()}
              style={({ pressed }) => [styles.whatsAppButton, pressed && styles.pressed]}
            >
              <MaterialCommunityIcons color="#FFFFFF" name="whatsapp" size={22} />
              <Text style={styles.whatsAppButtonText}>WhatsApp</Text>
            </Pressable>
            <Pressable
              accessibilityLabel={`Call ${order.customer.name || 'customer'}`}
              onPress={() => void openDialer()}
              style={({ pressed }) => [styles.callButton, pressed && styles.pressed]}
            >
              <MaterialCommunityIcons color="#0B5D3B" name="phone-outline" size={22} />
              <Text style={styles.callButtonText}>Call</Text>
            </Pressable>
          </View>
        ) : null}
      </View>

      <Text style={styles.sectionTitle}>Items ({order.items.length})</Text>
      <View style={styles.sectionCard}>
        {order.items.length ? order.items.map((item, index) => (
          <View key={`${item.sku || item.name}-${index}`} style={[styles.itemRow, index > 0 && styles.itemDivider]}>
            <View style={styles.itemCopy}>
              <Text style={styles.itemName}>{item.name}</Text>
              <Text style={styles.itemMeta}>Qty {item.quantity}{item.sku ? ` · SKU ${item.sku}` : ''}</Text>
            </View>
            <Text style={styles.itemTotal}>{money(item.total)}</Text>
          </View>
        )) : <Text style={styles.emptyText}>No item details supplied.</Text>}
      </View>

      <Text style={styles.sectionTitle}>Shipping</Text>
      <View style={styles.sectionCard}>
        <DetailRow label="Courier" value={order.courier_name} />
        <DetailRow label="Tracking number" value={order.tracking_number} />
        <DetailRow label="Shipping cost" value={money(order.shipping_cost)} />
        <DetailRow label="Order date" value={dateTime(order.order_date)} />
      </View>

      <Modal
        animationType="slide"
        onRequestClose={() => !submittingAction && setSelectedAction(null)}
        transparent
        visible={selectedAction !== null}
      >
        <View style={styles.modalBackdrop}>
          <View style={styles.actionModal}>
            <View style={styles.modalHeader}>
              <Text style={styles.modalTitle}>{selectedAction ? actionLabel(selectedAction) : ''}</Text>
              <Pressable disabled={submittingAction} onPress={() => setSelectedAction(null)} style={styles.modalClose}>
                <MaterialCommunityIcons color="#587066" name="close" size={24} />
              </Pressable>
            </View>
            <ScrollView keyboardShouldPersistTaps="handled" showsVerticalScrollIndicator={false}>
              {selectedAction?.required_fields.includes('customer_phone') ? (
                <View style={styles.formGroup}>
                  <Text style={styles.formLabel}>Customer mobile</Text>
                  <TextInput
                    autoComplete="tel"
                    keyboardType="phone-pad"
                    onChangeText={setCustomerPhone}
                    placeholder="10-digit mobile number"
                    placeholderTextColor="#82958D"
                    style={styles.formInput}
                    value={customerPhone}
                  />
                </View>
              ) : null}
              {selectedAction?.target_status === 'shipped' ? (
                <>
                  <View style={styles.formGroup}>
                    <Text style={styles.formLabel}>Courier partner</Text>
                    <TextInput onChangeText={setCourierName} placeholder="Example: India Post" placeholderTextColor="#82958D" style={styles.formInput} value={courierName} />
                  </View>
                  <View style={styles.formGroup}>
                    <Text style={styles.formLabel}>Tracking number</Text>
                    <TextInput
                      autoCapitalize="characters"
                      maxLength={13}
                      onChangeText={setTrackingNumber}
                      placeholder="AA123456789AA"
                      placeholderTextColor="#82958D"
                      style={styles.formInput}
                      value={trackingNumber}
                    />
                    <Text style={styles.formHint}>Enter 2 letters, 9 digits, then 2 letters.</Text>
                  </View>
                  <View style={styles.formGroup}>
                    <Text style={styles.formLabel}>Shipping cost</Text>
                    <TextInput keyboardType="decimal-pad" onChangeText={setShippingCost} placeholder="0.00" placeholderTextColor="#82958D" style={styles.formInput} value={shippingCost} />
                  </View>
                </>
              ) : null}
              {selectedAction?.target_status === 'order_cancelled' ? (
                <>
                  <Text style={styles.formLabel}>Cancellation reason</Text>
                  <View style={styles.reasonGrid}>
                    {CANCELLATION_REASONS.map((reason) => (
                      <Pressable
                        key={reason.code}
                        onPress={() => setCancellationReason(reason.code)}
                        style={[styles.reasonChip, cancellationReason === reason.code && styles.reasonChipActive]}
                      >
                        <Text style={[styles.reasonChipText, cancellationReason === reason.code && styles.reasonChipTextActive]}>{reason.label}</Text>
                      </Pressable>
                    ))}
                  </View>
                  <View style={styles.formGroup}>
                    <Text style={styles.formLabel}>Note (optional)</Text>
                    <TextInput
                      maxLength={255}
                      multiline
                      onChangeText={setCancellationNote}
                      placeholder="Add a short explanation"
                      placeholderTextColor="#82958D"
                      style={[styles.formInput, styles.formTextArea]}
                      value={cancellationNote}
                    />
                  </View>
                  <View style={styles.cancelWarning}><Text style={styles.cancelWarningText}>Cancellation cannot be undone from the app.</Text></View>
                </>
              ) : null}
            </ScrollView>
            <Pressable
              disabled={!actionFormReady || submittingAction}
              onPress={submitActionForm}
              style={[
                styles.confirmActionButton,
                selectedAction?.target_status === 'order_cancelled' && styles.confirmCancelButton,
                (!actionFormReady || submittingAction) && styles.disabledButton,
              ]}
            >
              {submittingAction ? <ActivityIndicator color="#FFFFFF" /> : <Text style={styles.confirmActionText}>Confirm {selectedAction ? actionLabel(selectedAction).toLowerCase() : 'action'}</Text>}
            </Pressable>
          </View>
        </View>
      </Modal>

    </ScrollView>
  );
}

export default function OrdersScreen({
  initialFilters = {},
  initialOrderId = null,
}: {
  initialFilters?: OrderListFilters;
  initialOrderId?: number | null;
}) {
  const { runAuthenticated } = useAuth();
  const [orders, setOrders] = useState<OrderSummary[]>([]);
  const [draftSearch, setDraftSearch] = useState('');
  const [search, setSearch] = useState('');
  const [status, setStatus] = useState(initialFilters.status || '');
  const dateFrom = initialFilters.date_from || '';
  const dateTo = initialFilters.date_to || '';
  const [nextCursor, setNextCursor] = useState<string | null>(null);
  const [selectedOrderId, setSelectedOrderId] = useState<number | null>(initialOrderId);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [loadingMore, setLoadingMore] = useState(false);
  const [error, setError] = useState('');

  const loadFirstPage = useCallback(async (refresh = false) => {
    if (refresh) setRefreshing(true); else setLoading(true);
    setError('');
    try {
      const response = await runAuthenticated((token) => api.orders(token, {
        search,
        status,
        date_from: dateFrom,
        date_to: dateTo,
      }));
      setOrders(response.data);
      setNextCursor(response.pagination.next_cursor);
    } catch (reason) {
      setError(reason instanceof api.ApiError ? reason.message : 'Orders could not be loaded.');
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }, [dateFrom, dateTo, runAuthenticated, search, status]);

  useEffect(() => { void loadFirstPage(); }, [loadFirstPage]);

  const loadMore = async () => {
    if (!nextCursor || loadingMore) return;
    setLoadingMore(true);
    setError('');
    try {
      const response = await runAuthenticated((token) => api.orders(token, {
        search,
        status,
        date_from: dateFrom,
        date_to: dateTo,
        cursor: nextCursor,
      }));
      setOrders((current) => [...current, ...response.data]);
      setNextCursor(response.pagination.next_cursor);
    } catch (reason) {
      setError(reason instanceof api.ApiError ? reason.message : 'More orders could not be loaded.');
    } finally {
      setLoadingMore(false);
    }
  };

  if (selectedOrderId !== null) return <OrderDetailScreen orderId={selectedOrderId} onBack={() => setSelectedOrderId(null)} />;

  const listHeader = (
    <View>
      {dateFrom && dateTo ? (
        <View style={styles.scopeBanner}>
          <Text style={styles.scopeLabel}>Current month</Text>
          <Text style={styles.scopeDates}>{dateFrom} to {dateTo}</Text>
        </View>
      ) : null}
      <View style={styles.searchRow}>
        <TextInput
          accessibilityLabel="Search orders"
          autoCapitalize="none"
          onChangeText={setDraftSearch}
          onSubmitEditing={() => setSearch(draftSearch.trim())}
          placeholder="Order, tracking, or customer"
          placeholderTextColor="#82958D"
          returnKeyType="search"
          style={styles.searchInput}
          value={draftSearch}
        />
        <Pressable onPress={() => setSearch(draftSearch.trim())} style={styles.searchButton}><Text style={styles.searchButtonText}>Search</Text></Pressable>
      </View>
      <ScrollView horizontal showsHorizontalScrollIndicator={false} contentContainerStyle={styles.filterRow}>
        {STATUS_FILTERS.map((filter) => (
          <Pressable key={filter.code} onPress={() => setStatus(filter.code)} style={[styles.filterChip, status === filter.code && styles.filterChipActive]}>
            <Text style={[styles.filterText, status === filter.code && styles.filterTextActive]}>{filter.label}</Text>
          </Pressable>
        ))}
      </ScrollView>
      {error && orders.length ? <View style={styles.warning}><Text style={styles.warningText}>{error}</Text></View> : null}
      <Text style={styles.resultLabel}>{orders.length} order{orders.length === 1 ? '' : 's'} loaded</Text>
    </View>
  );

  if (loading && !orders.length) return <View style={styles.center}><ActivityIndicator size="large" color="#0B5D3B" /><Text style={styles.loadingText}>Loading orders...</Text></View>;
  if (error && !orders.length) return (
    <View style={styles.center}>
      <Text style={styles.errorTitle}>Orders unavailable</Text>
      <Text style={styles.errorMessage}>{error}</Text>
      <Pressable onPress={() => void loadFirstPage()} style={styles.primaryButton}><Text style={styles.primaryButtonText}>Try again</Text></Pressable>
    </View>
  );

  return (
    <FlatList
      contentContainerStyle={styles.listContent}
      data={orders}
      keyExtractor={(item) => String(item.id)}
      ListEmptyComponent={<View style={styles.emptyState}><Text style={styles.emptyTitle}>No matching orders</Text><Text style={styles.emptyText}>Try another search or status filter.</Text></View>}
      ListFooterComponent={nextCursor ? (
        <Pressable disabled={loadingMore} onPress={() => void loadMore()} style={styles.loadMoreButton}>
          {loadingMore ? <ActivityIndicator color="#0B5D3B" /> : <Text style={styles.loadMoreText}>Load more orders</Text>}
        </Pressable>
      ) : orders.length ? <Text style={styles.endText}>All matching orders loaded</Text> : null}
      ListHeaderComponent={listHeader}
      refreshControl={<RefreshControl refreshing={refreshing} onRefresh={() => void loadFirstPage(true)} colors={['#0B5D3B']} tintColor="#0B5D3B" />}
      renderItem={({ item }) => <OrderCard order={item} onPress={() => setSelectedOrderId(item.id)} />}
    />
  );
}

const styles = StyleSheet.create({
  center: { flex: 1, alignItems: 'center', justifyContent: 'center', padding: 30 },
  loadingText: { color: '#587066', marginTop: 14, fontWeight: '600' },
  listContent: { padding: 18, paddingBottom: 28 },
  scopeBanner: { backgroundColor: '#E4F3EB', borderRadius: 12, marginBottom: 12, paddingHorizontal: 14, paddingVertical: 10 },
  scopeLabel: { color: '#0B5D3B', fontSize: 13, fontWeight: '800' },
  scopeDates: { color: '#587066', fontSize: 11, marginTop: 2 },
  searchRow: { flexDirection: 'row', marginBottom: 12 },
  searchInput: { flex: 1, minHeight: 48, backgroundColor: '#FFFFFF', borderColor: '#CBD9D3', borderWidth: 1, borderRadius: 13, paddingHorizontal: 14, color: '#17352A', fontSize: 15 },
  searchButton: { minWidth: 78, minHeight: 48, backgroundColor: '#0B5D3B', borderRadius: 13, alignItems: 'center', justifyContent: 'center', marginLeft: 8 },
  searchButtonText: { color: '#FFFFFF', fontWeight: '800' },
  filterRow: { paddingBottom: 12, columnGap: 8 },
  filterChip: { borderColor: '#CBD9D3', borderWidth: 1, borderRadius: 20, backgroundColor: '#FFFFFF', paddingHorizontal: 14, paddingVertical: 9 },
  filterChipActive: { backgroundColor: '#0B5D3B', borderColor: '#0B5D3B' },
  filterText: { color: '#587066', fontSize: 13, fontWeight: '700' },
  filterTextActive: { color: '#FFFFFF' },
  resultLabel: { color: '#71867D', fontSize: 12, fontWeight: '700', marginBottom: 10 },
  orderCard: { backgroundColor: '#FFFFFF', borderColor: '#DEE7E3', borderWidth: 1, borderRadius: 16, padding: 16, marginBottom: 11 },
  orderTopRow: { flexDirection: 'row', justifyContent: 'space-between', alignItems: 'flex-start' },
  orderReferenceWrap: { flex: 1, paddingRight: 8 },
  orderReference: { color: '#17352A', fontSize: 16, fontWeight: '900' },
  orderSource: { color: '#71867D', fontSize: 12, marginTop: 4 },
  statusPill: { backgroundColor: '#E4F3EB', borderRadius: 18, paddingHorizontal: 10, paddingVertical: 6 },
  statusPillCritical: { backgroundColor: '#FDE8E7' },
  statusText: { color: '#147348', fontSize: 11, fontWeight: '800' },
  statusTextCritical: { color: '#B42318' },
  paymentPill: { borderRadius: 12, paddingHorizontal: 8, paddingVertical: 4 },
  paymentPillReceived: { backgroundColor: '#E4F3EB' },
  paymentPillPending: { backgroundColor: '#FFF4D8' },
  paymentPillText: { fontSize: 10, fontWeight: '800' },
  paymentPillTextReceived: { color: '#147348' },
  paymentPillTextPending: { color: '#9A5B00' },
  customerName: { color: '#29483D', fontSize: 15, fontWeight: '700', marginTop: 14 },
  orderBottomRow: { flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center', marginTop: 12 },
  orderSummaryCopy: { flex: 1, paddingRight: 12 },
  orderMetaRow: { flexDirection: 'row', alignItems: 'center', columnGap: 8 },
  orderMeta: { color: '#71867D', fontSize: 12 },
  trackingText: { color: '#587066', fontSize: 12, fontWeight: '700', marginTop: 7 },
  orderTotal: { color: '#17352A', fontSize: 16, fontWeight: '900' },
  warning: { backgroundColor: '#FFF4D8', borderColor: '#F0D08D', borderWidth: 1, borderRadius: 12, padding: 12, marginBottom: 12 },
  warningText: { color: '#7A4A00', lineHeight: 19 },
  emptyState: { alignItems: 'center', paddingVertical: 48 },
  emptyTitle: { color: '#17352A', fontSize: 20, fontWeight: '800' },
  emptyText: { color: '#71867D', lineHeight: 20, textAlign: 'center', marginTop: 7 },
  loadMoreButton: { minHeight: 50, borderColor: '#0B5D3B', borderWidth: 1, borderRadius: 14, alignItems: 'center', justifyContent: 'center', marginTop: 5 },
  loadMoreText: { color: '#0B5D3B', fontWeight: '800' },
  endText: { color: '#82958D', textAlign: 'center', fontSize: 12, marginVertical: 14 },
  errorTitle: { color: '#17352A', fontSize: 21, fontWeight: '800', textAlign: 'center' },
  errorMessage: { color: '#587066', lineHeight: 21, textAlign: 'center', marginTop: 8 },
  primaryButton: { backgroundColor: '#0B5D3B', minHeight: 48, borderRadius: 13, paddingHorizontal: 24, alignItems: 'center', justifyContent: 'center', marginTop: 20 },
  primaryButtonText: { color: '#FFFFFF', fontWeight: '800' },
  detailContent: { padding: 18, paddingBottom: 32 },
  backButton: { alignSelf: 'flex-start', paddingVertical: 8, paddingRight: 14, marginBottom: 8 },
  backText: { color: '#0B5D3B', fontSize: 15, fontWeight: '800' },
  detailHero: { backgroundColor: '#FFFFFF', borderColor: '#DEE7E3', borderWidth: 1, borderRadius: 18, padding: 18, marginBottom: 22 },
  detailReference: { color: '#17352A', fontSize: 21, fontWeight: '900' },
  heroTotals: { borderTopColor: '#E4EAE7', borderTopWidth: 1, marginTop: 18, paddingTop: 14, flexDirection: 'row', alignItems: 'flex-end', justifyContent: 'space-between' },
  heroTotal: { color: '#17352A', fontSize: 24, fontWeight: '900' },
  paymentText: { color: '#587066', fontSize: 12, fontWeight: '700' },
  sectionTitle: { color: '#17352A', fontSize: 18, fontWeight: '800', marginBottom: 10 },
  sectionCard: { backgroundColor: '#FFFFFF', borderColor: '#E0E7E3', borderWidth: 1, borderRadius: 16, padding: 16, marginBottom: 22 },
  actionHelp: { color: '#71867D', fontSize: 12, lineHeight: 18, marginBottom: 12 },
  actionList: { flexDirection: 'row', flexWrap: 'wrap', gap: 9 },
  actionButton: { flexBasis: '48%', flexGrow: 1, minHeight: 50, borderColor: '#B8D5C8', borderWidth: 1, borderRadius: 13, backgroundColor: '#F4FAF7', paddingHorizontal: 12, flexDirection: 'row', alignItems: 'center', justifyContent: 'center', columnGap: 8 },
  actionButtonText: { color: '#0B5D3B', fontSize: 14, fontWeight: '800' },
  destructiveActionButton: { backgroundColor: '#FFF6F5', borderColor: '#F1C0BC' },
  destructiveActionText: { color: '#B42318' },
  detailRow: { marginBottom: 13 },
  detailLabel: { color: '#71867D', fontSize: 11, fontWeight: '800', letterSpacing: 0.6, textTransform: 'uppercase' },
  detailValue: { color: '#29483D', fontSize: 15, lineHeight: 21, fontWeight: '600', marginTop: 4 },
  maskedNote: { color: '#7A4A00', backgroundColor: '#FFF4D8', borderRadius: 9, padding: 10, lineHeight: 18 },
  contactActions: { borderTopColor: '#E7ECEA', borderTopWidth: 1, flexDirection: 'row', columnGap: 10, marginTop: 4, paddingTop: 14 },
  whatsAppButton: { flex: 1, minHeight: 48, backgroundColor: '#128C4A', borderRadius: 13, flexDirection: 'row', alignItems: 'center', justifyContent: 'center', columnGap: 8 },
  whatsAppButtonText: { color: '#FFFFFF', fontWeight: '800' },
  callButton: { flex: 1, minHeight: 48, backgroundColor: '#FFFFFF', borderColor: '#0B5D3B', borderWidth: 1, borderRadius: 13, flexDirection: 'row', alignItems: 'center', justifyContent: 'center', columnGap: 8 },
  callButtonText: { color: '#0B5D3B', fontWeight: '800' },
  itemRow: { flexDirection: 'row', alignItems: 'center', paddingVertical: 6 },
  itemDivider: { borderTopColor: '#E7ECEA', borderTopWidth: 1, paddingTop: 13, marginTop: 7 },
  itemCopy: { flex: 1, paddingRight: 12 },
  itemName: { color: '#29483D', fontSize: 15, fontWeight: '800' },
  itemMeta: { color: '#71867D', fontSize: 12, marginTop: 4 },
  itemTotal: { color: '#17352A', fontWeight: '900' },
  modalBackdrop: { flex: 1, backgroundColor: 'rgba(15, 35, 28, 0.52)', justifyContent: 'flex-end' },
  actionModal: { maxHeight: '88%', backgroundColor: '#FFFFFF', borderTopLeftRadius: 24, borderTopRightRadius: 24, paddingHorizontal: 20, paddingTop: 18, paddingBottom: 22 },
  modalHeader: { flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', marginBottom: 18 },
  modalTitle: { flex: 1, color: '#17352A', fontSize: 20, fontWeight: '900', paddingRight: 12 },
  modalClose: { width: 42, height: 42, borderRadius: 21, backgroundColor: '#F1F5F3', alignItems: 'center', justifyContent: 'center' },
  formGroup: { marginBottom: 16 },
  formLabel: { color: '#29483D', fontSize: 13, fontWeight: '800', marginBottom: 7 },
  formInput: { minHeight: 49, borderColor: '#CBD9D3', borderWidth: 1, borderRadius: 12, backgroundColor: '#FFFFFF', color: '#17352A', fontSize: 15, paddingHorizontal: 14 },
  formTextArea: { minHeight: 86, paddingTop: 12, textAlignVertical: 'top' },
  formHint: { color: '#71867D', fontSize: 11, marginTop: 6 },
  reasonGrid: { flexDirection: 'row', flexWrap: 'wrap', gap: 8, marginBottom: 18 },
  reasonChip: { borderColor: '#CBD9D3', borderWidth: 1, borderRadius: 18, backgroundColor: '#FFFFFF', paddingHorizontal: 12, paddingVertical: 9 },
  reasonChipActive: { backgroundColor: '#0B5D3B', borderColor: '#0B5D3B' },
  reasonChipText: { color: '#587066', fontSize: 12, fontWeight: '700' },
  reasonChipTextActive: { color: '#FFFFFF' },
  cancelWarning: { backgroundColor: '#FFF4D8', borderRadius: 10, padding: 11, marginBottom: 16 },
  cancelWarningText: { color: '#7A4A00', fontSize: 12, lineHeight: 18, fontWeight: '700' },
  confirmActionButton: { minHeight: 52, borderRadius: 14, backgroundColor: '#0B5D3B', alignItems: 'center', justifyContent: 'center', marginTop: 4 },
  confirmCancelButton: { backgroundColor: '#B42318' },
  confirmActionText: { color: '#FFFFFF', fontSize: 15, fontWeight: '900' },
  disabledButton: { opacity: 0.42 },
  pressed: { opacity: 0.65 },
});
