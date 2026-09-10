import { useCallback, useEffect, useState } from 'react';
import MaterialCommunityIcons from '@expo/vector-icons/MaterialCommunityIcons';
import { CameraView, useCameraPermissions, type BarcodeScanningResult } from 'expo-camera';
import * as Clipboard from 'expo-clipboard';
import * as ImagePicker from 'expo-image-picker';
import * as Print from 'expo-print';
import * as Sharing from 'expo-sharing';
import {
  ActivityIndicator,
  Alert,
  AppState,
  BackHandler,
  FlatList,
  Image,
  KeyboardAvoidingView,
  Linking,
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
import { useSafeAreaInsets } from 'react-native-safe-area-context';

import * as api from '../auth/api';
import { useAuth } from '../auth/AuthContext';
import type { Money, OrderAction, OrderDetail, OrderIssueFlagUpdate, OrderListFilters, OrderStatusUpdate, OrderSummary, ShippingAddress } from './types';
import type { ProductSummary } from '../stock/types';

const STATUS_FILTERS = [
  { code: '', label: 'All' },
  { code: 'waiting_order', label: 'Waiting' },
  { code: 'new_order', label: 'New' },
  { code: 'order_accepted', label: 'Accepted' },
  { code: 'order_packed', label: 'Packed' },
  { code: 'shipped', label: 'Shipped' },
  { code: 'delivery_issue', label: 'Attention' },
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

const ISSUE_REASONS = [
  { code: 'address_issue', label: 'Address issue' },
  { code: 'payment_issue', label: 'Payment issue' },
  { code: 'stock_issue', label: 'Stock issue' },
  { code: 'courier_issue', label: 'Courier issue' },
  { code: 'customer_unreachable', label: 'Customer unreachable' },
  { code: 'other', label: 'Other' },
];

const COURIER_PARTNERS = [
  'India Post',
  'Delhivery',
  'DTDC',
  'Blue Dart',
  'Xpressbees',
  'Ecom Express',
  'Shadowfax',
  'Amazon Shipping',
  'Self-Ship',
];

const INDIA_POST_CUSTOMER_ID = '1828524916';

type ManualEditItem = {
  productId: number;
  name: string;
  sku: string | null;
  quantity: number;
  unitPrice: number;
};

type PackingImageDraft = {
  uri: string;
  base64: string;
  name: string;
  type: string;
};

const ACTION_LABELS: Record<string, string> = {
  order_accepted: 'Accept order',
  order_packed: 'Mark packed',
  shipped: 'Mark shipped',
  out_for_delivery: 'Mark out for delivery',
  delivered: 'Mark delivered',
  completed: 'Mark completed',
  order_cancelled: 'Cancel order',
};

function actionLabel(action: OrderAction) {
  if (action.code === 'mark_payment_received') return 'Mark payment received';
  if (action.code === 'flag_issue') return 'Flag issue';
  return ACTION_LABELS[action.target_status || ''] || action.label;
}

function newIdempotencyKey() {
  return `android-${Date.now()}-${Math.random().toString(36).slice(2, 12)}`;
}

function money(value: Money | null | undefined) {
  if (!value) return 'Not available';
  return `${value.currency === 'INR' ? '₹' : value.currency} ${value.amount}`;
}

function parseMoneyAmount(value: string | null | undefined) {
  if (!value) return 0;
  const parsed = Number(String(value).replace(/,/g, '').trim());
  return Number.isFinite(parsed) ? parsed : 0;
}

function formatInrAmount(value: number) {
  return `₹ ${value.toFixed(2)}`;
}

function normalizeTrackingBarcode(value: string) {
  const cleaned = String(value || '').replace(/[^A-Za-z0-9]/g, '').toUpperCase();
  const match = cleaned.match(/[A-Z]{2}\d{9}[A-Z]{2}/);
  return match ? match[0] : cleaned;
}

function productUnitPrice(product: ProductSummary) {
  return parseMoneyAmount(product.prices?.sale?.amount || product.prices?.regular?.amount);
}

function dateTime(value: string | null | undefined) {
  if (!value) return 'Not available';
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return 'Not available';
  return parsed.toLocaleString([], { day: '2-digit', month: 'short', hour: '2-digit', minute: '2-digit' });
}

function actionErrorMessage(reason: unknown) {
  if (!(reason instanceof api.ApiError)) return 'Please try again.';
  const fieldMessages = Object.entries(reason.fields)
    .flatMap(([field, messages]) => messages.map((message) => `${field.replaceAll('_', ' ')}: ${message}`));
  return [reason.message, ...fieldMessages, reason.requestId ? `Request ID: ${reason.requestId}` : '']
    .filter(Boolean)
    .join('\n\n');
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
      <View style={styles.orderCardBody}>
        <View style={styles.orderTopRow}>
          <View style={styles.orderReferenceWrap}>
            <Text numberOfLines={1} style={styles.orderReference}>{order.customer_display_name || 'Customer unavailable'}</Text>
            <Text numberOfLines={1} style={styles.orderSource}>Order {order.reference} · {dateTime(order.order_date)}</Text>
          </View>
          <StatusPill order={order} />
        </View>
        <View style={styles.orderBottomRow}>
          <View style={styles.orderMetaRow}>
            <Text style={styles.orderMeta}>{order.item_count} item{order.item_count === 1 ? '' : 's'}</Text>
            <PaymentPill order={order} />
          </View>
          <Text style={styles.orderTotal}>{money(order.total)}</Text>
        </View>
        {order.status.code === 'shipped' && order.tracking_number ? (
          <View style={styles.trackingRow}>
            <MaterialCommunityIcons color="#1769C2" name="truck-fast-outline" size={15} />
            <Text numberOfLines={1} selectable style={styles.trackingText}>{order.tracking_number}</Text>
          </View>
        ) : null}
      </View>
      <MaterialCommunityIcons color="#63766E" name="chevron-right" size={23} />
    </Pressable>
  );
}

function escapeHtml(value: string | null | undefined) {
  return String(value || '')
    .replaceAll('&', '&amp;')
    .replaceAll('<', '&lt;')
    .replaceAll('>', '&gt;')
    .replaceAll('"', '&quot;')
    .replaceAll("'", '&#039;');
}

function shippingLabelHtml(order: OrderDetail) {
  const sender = order.shipping_label?.sender || {
    name: 'Mathukai Organic',
    phone: null,
    address: null,
  };
  const payment = order.payment_state.code === 'received' || order.payment_state.code === 'paid'
    ? 'PAID'
    : `COLLECT ${escapeHtml(money(order.total))}`;
  return `<!doctype html>
<html><head><meta charset="utf-8"><style>
@page { size: 4in 6in; margin: 0; }
* { box-sizing: border-box; }
body { margin: 0; padding: 0.18in; width: 4in; height: 6in; font-family: Arial, sans-serif; color: #111; }
.label { height: 100%; border: 2px solid #111; padding: 0.14in; }
.header { border-bottom: 2px solid #111; padding-bottom: 0.12in; }
.brand { font-size: 14px; font-weight: 800; letter-spacing: 0.8px; margin-bottom: 5px; }
h1 { font-size: 20px; margin: 0 0 6px; }
.reference { font-size: 15px; font-weight: 700; }
.meta { font-size: 11px; margin-top: 4px; }
.box { border: 1.5px solid #111; margin-top: 0.14in; padding: 0.12in; }
.eyebrow { font-size: 10px; font-weight: 700; margin-bottom: 8px; }
.name { font-size: 18px; font-weight: 700; margin-bottom: 7px; }
.address { font-size: 14px; line-height: 1.35; }
.phone { font-size: 13px; font-weight: 700; margin-top: 8px; }
.from .name { font-size: 14px; }
.from .address { font-size: 12px; }
.footer { display: flex; justify-content: space-between; border-top: 2px solid #111; margin-top: 0.14in; padding-top: 0.12in; font-size: 12px; font-weight: 700; }
</style></head><body><div class="label">
<div class="header"><div class="brand">MATHUKAI ORGANIC</div><h1>SHIPPING LABEL</h1><div class="reference">Order ${escapeHtml(order.reference)}</div>
${order.courier_name ? `<div class="meta">Courier: ${escapeHtml(order.courier_name)}</div>` : ''}
${order.tracking_number ? `<div class="meta">Tracking: ${escapeHtml(order.tracking_number)}</div>` : ''}
<div class="meta">India Post Customer ID: ${INDIA_POST_CUSTOMER_ID}</div>
${order.package_weight_grams ? `<div class="meta">Weight: ${escapeHtml(order.package_weight_grams)} g</div>` : ''}</div>
<div class="box"><div class="eyebrow">TO</div><div class="name">${escapeHtml(order.customer.name || 'Customer')}</div>
<div class="address">${escapeHtml(order.customer.delivery_address || 'Delivery address unavailable')}</div>
${order.customer.phone ? `<div class="phone">Phone: ${escapeHtml(order.customer.phone)}</div>` : ''}</div>
<div class="box from"><div class="eyebrow">FROM</div><div class="name">${escapeHtml(sender.name)}</div>
<div class="address">${escapeHtml(sender.address || 'Sender address unavailable')}</div>
${sender.phone ? `<div class="phone">Phone: ${escapeHtml(sender.phone)}</div>` : ''}</div>
<div class="footer"><span>${order.item_count} item${order.item_count === 1 ? '' : 's'}</span><span>${payment}</span></div>
</div></body></html>`;
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
  const insets = useSafeAreaInsets();
  const [cameraPermission, requestCameraPermission] = useCameraPermissions();
  const [order, setOrder] = useState<OrderDetail | null>(null);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState('');
  const [selectedAction, setSelectedAction] = useState<OrderAction | null>(null);
  const [submittingAction, setSubmittingAction] = useState(false);
  const [actionProgressLabel, setActionProgressLabel] = useState('Updating order...');
  const [actionFeedback, setActionFeedback] = useState('');
  const [customerPhone, setCustomerPhone] = useState('');
  const [courierName, setCourierName] = useState('');
  const [courierMenuOpen, setCourierMenuOpen] = useState(false);
  const [customCourier, setCustomCourier] = useState(false);
  const [trackingNumber, setTrackingNumber] = useState('');
  const [trackingScannerVisible, setTrackingScannerVisible] = useState(false);
  const [trackingScanned, setTrackingScanned] = useState(false);
  const [packageWeightGrams, setPackageWeightGrams] = useState('');
  const [packingImageUrl, setPackingImageUrl] = useState('');
  const [packingImageDraft, setPackingImageDraft] = useState<PackingImageDraft | null>(null);
  const [shippingCost, setShippingCost] = useState('');
  const [cancellationReason, setCancellationReason] = useState('');
  const [cancellationNote, setCancellationNote] = useState('');
  const [issueReason, setIssueReason] = useState('address_issue');
  const [issueNote, setIssueNote] = useState('');
  const [labelPreviewVisible, setLabelPreviewVisible] = useState(false);
  const [labelBusy, setLabelBusy] = useState(false);
  const [addressEditorVisible, setAddressEditorVisible] = useState(false);
  const [addressSaving, setAddressSaving] = useState(false);
  const [addressError, setAddressError] = useState('');
  const [shippingAddress, setShippingAddress] = useState<ShippingAddress>({
    name: '',
    address_1: '',
    address_2: '',
    city: '',
    state: '',
    pincode: '',
    country: '',
  });
  const [manualEditVisible, setManualEditVisible] = useState(false);
  const [manualEditSearch, setManualEditSearch] = useState('');
  const [manualEditProducts, setManualEditProducts] = useState<ProductSummary[]>([]);
  const [manualEditItems, setManualEditItems] = useState<ManualEditItem[]>([]);
  const [manualEditShippingMode, setManualEditShippingMode] = useState<'free' | 'charged'>('free');
  const [manualEditShippingCost, setManualEditShippingCost] = useState('');
  const [manualEditDiscountCost, setManualEditDiscountCost] = useState('');
  const [manualEditLoading, setManualEditLoading] = useState(false);
  const [manualEditSaving, setManualEditSaving] = useState(false);
  const [manualEditError, setManualEditError] = useState('');

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

  useEffect(() => {
    if (!actionFeedback) return undefined;
    const timeout = setTimeout(() => setActionFeedback(''), 4500);
    return () => clearTimeout(timeout);
  }, [actionFeedback]);

  const closeShippingLabel = useCallback(() => {
    setLabelPreviewVisible(false);
  }, []);

  useEffect(() => {
    const subscription = BackHandler.addEventListener('hardwareBackPress', () => {
      if (labelPreviewVisible) {
        closeShippingLabel();
        return true;
      }
      if (addressEditorVisible) {
        if (!addressSaving) setAddressEditorVisible(false);
        return true;
      }
      if (manualEditVisible) {
        if (!manualEditSaving) setManualEditVisible(false);
        return true;
      }
      if (selectedAction) {
        if (!submittingAction) setSelectedAction(null);
        return true;
      }
      if (!submittingAction) onBack();
      return true;
    });
    return () => subscription.remove();
  }, [
    addressEditorVisible,
    addressSaving,
    closeShippingLabel,
    labelPreviewVisible,
    manualEditSaving,
    manualEditVisible,
    onBack,
    selectedAction,
    submittingAction,
  ]);

  const applyMutationResult = (updatedOrder: OrderDetail, warningMessages: string[]) => {
    setOrder(updatedOrder);
    setSelectedAction(null);
    if (warningMessages.length) {
      setActionFeedback('Order updated with a warning.');
      Alert.alert('Order updated with a warning', warningMessages.join('\n'));
    } else {
      setActionFeedback('Order updated successfully.');
    }
  };

  const handleMutationError = async (reason: unknown) => {
    if (reason instanceof api.ApiError && reason.status === 409) {
      await load(true);
      Alert.alert(
        'Order was refreshed',
        `This order changed before your action was saved. Review it and try again.${reason.requestId ? `\n\nRequest ID: ${reason.requestId}` : ''}`,
      );
      return;
    }
    await load(true);
    Alert.alert('Action not completed', actionErrorMessage(reason));
  };

  const submitStatusAction = async (action: OrderAction, values: Partial<OrderStatusUpdate> = {}) => {
    if (!order || !action.target_status || submittingAction) return;
    setActionFeedback('');
    setActionProgressLabel(`${actionLabel(action)}...`);
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
    setActionFeedback('');
    setActionProgressLabel('Updating payment...');
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

  const submitIssueFlag = async () => {
    if (!order || submittingAction) return;
    const values: OrderIssueFlagUpdate = {
      expected_version: order.version,
      reason: issueReason,
      note: issueNote.trim(),
    };
    setActionFeedback('');
    setActionProgressLabel('Flagging issue...');
    setSubmittingAction(true);
    try {
      const response = await runAuthenticated((token) => api.flagOrderIssue(
        token,
        order.id,
        values,
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
    if (action.code === 'flag_issue') {
      setIssueReason(order?.issue_flag?.reason || 'address_issue');
      setIssueNote(order?.issue_flag?.note || '');
      setSelectedAction(action);
      return;
    }
    const needsForm = action.required_fields.length > 0 || action.reason_required;
    if (needsForm) {
      setCustomerPhone(order?.customer.phone || '');
      setCourierName(order?.courier_name || '');
      setCustomCourier(Boolean(order?.courier_name && !COURIER_PARTNERS.includes(order.courier_name)));
      setCourierMenuOpen(false);
      setTrackingNumber(order?.tracking_number || '');
      setPackageWeightGrams(order?.package_weight_grams || '');
      setPackingImageUrl(order?.packing_image_url || '');
      setPackingImageDraft(null);
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
    if (selectedAction.code === 'flag_issue') {
      void submitIssueFlag();
      return;
    }
    const values: Partial<OrderStatusUpdate> = {};
    if (selectedAction.required_fields.includes('customer_phone')) values.customer_phone = customerPhone.trim();
    if (selectedAction.target_status === 'order_packed') {
      values.tracking_number = trackingNumber.trim().toUpperCase();
      values.package_weight_grams = packageWeightGrams.trim();
      values.packing_image_url = packingImageUrl.trim();
      if (packingImageDraft) {
        values.packing_image_base64 = packingImageDraft.base64;
        values.packing_image_name = packingImageDraft.name;
        values.packing_image_type = packingImageDraft.type;
      }
    }
    if (selectedAction.target_status === 'shipped') {
      values.shipping_base_amount = shippingCost.trim();
    }
    if (selectedAction.target_status === 'order_cancelled') {
      values.cancellation_reason = cancellationReason;
      values.cancellation_note = cancellationNote.trim();
    }
    void submitStatusAction(selectedAction, values);
  };

  const openTrackingScanner = async () => {
    if (!cameraPermission?.granted) {
      const permission = await requestCameraPermission();
      if (!permission.granted) {
        Alert.alert('Camera permission needed', 'Allow camera access to scan the tracking barcode.');
        return;
      }
    }
    setTrackingScanned(false);
    setTrackingScannerVisible(true);
  };

  const handleTrackingBarcode = (result: BarcodeScanningResult) => {
    if (trackingScanned) return;
    const code = normalizeTrackingBarcode(result.data);
    if (!code) return;
    setTrackingScanned(true);
    setTrackingNumber(code);
    setTrackingScannerVisible(false);
    if (!/^[A-Z]{2}\d{9}[A-Z]{2}$/.test(code)) {
      Alert.alert('Barcode scanned', 'Tracking number filled. Please check the value before saving.');
    }
  };

  const openAddressEditor = () => {
    if (!order?.customer.shipping_address) return;
    setShippingAddress({
      ...order.customer.shipping_address,
      name: order.customer.shipping_address.name || order.customer.name || '',
    });
    setAddressError('');
    setAddressEditorVisible(true);
  };

  const updateAddressField = (field: keyof ShippingAddress, value: string) => {
    setShippingAddress((current) => ({ ...current, [field]: value }));
  };

  const submitShippingAddress = async () => {
    if (!order || addressSaving) return;
    if (!shippingAddress.name.trim() || !shippingAddress.address_1.trim() || !shippingAddress.city.trim() || !shippingAddress.state.trim() || shippingAddress.pincode.trim().length < 3) {
      setAddressError('Complete customer name, address line 1, city, state, and a valid pincode.');
      return;
    }
    setAddressSaving(true);
    setAddressError('');
    try {
      const response = await runAuthenticated((token) => api.updateOrderShippingAddress(
        token,
        order.id,
        {
          ...shippingAddress,
          name: shippingAddress.name.trim(),
          address_1: shippingAddress.address_1.trim(),
          address_2: shippingAddress.address_2.trim(),
          city: shippingAddress.city.trim(),
          state: shippingAddress.state.trim(),
          pincode: shippingAddress.pincode.trim(),
          country: shippingAddress.country.trim(),
          expected_version: order.version,
        },
        newIdempotencyKey(),
      ));
      setOrder(response.data.order);
      setAddressEditorVisible(false);
      setActionFeedback('Delivery details updated successfully.');
      await load(true);
    } catch (reason) {
      if (reason instanceof api.ApiError && reason.status === 409) {
        setAddressEditorVisible(false);
        await load(true);
        Alert.alert('Order was refreshed', 'The order changed before the address was saved. Review it and try again.');
      } else {
        setAddressError(reason instanceof api.ApiError ? reason.message : 'The shipping address could not be updated.');
      }
    } finally {
      setAddressSaving(false);
    }
  };

  const loadManualEditProducts = async () => {
    setManualEditLoading(true);
    setManualEditError('');
    try {
      const response = await runAuthenticated((token) => api.products(token, { search: manualEditSearch }));
      setManualEditProducts(response.data.slice(0, 25));
    } catch (reason) {
      setManualEditError(reason instanceof api.ApiError ? reason.message : 'Products could not be loaded.');
    } finally {
      setManualEditLoading(false);
    }
  };

  const openManualOrderEditor = async () => {
    if (!order?.can_edit_manual_order) return;
    const editableItems = order.items
      .filter((item) => item.product_id !== null && item.line_type !== 'shipping' && item.line_type !== 'discount')
      .map((item) => {
        const quantity = Math.max(1, item.quantity || 1);
        const lineTotal = parseMoneyAmount(item.total?.amount);
        return {
          productId: item.product_id as number,
          name: item.name,
          sku: item.sku,
          quantity,
          unitPrice: quantity > 0 ? lineTotal / quantity : lineTotal,
        };
      });
    setManualEditItems(editableItems);
    const shippingTotal = parseMoneyAmount(order.shipping_total?.amount);
    const discountTotal = order.items
      .filter((item) => item.line_type === 'discount')
      .reduce((sum, item) => sum + Math.abs(parseMoneyAmount(item.total?.amount)), 0);
    setManualEditShippingMode(shippingTotal > 0 ? 'charged' : 'free');
    setManualEditShippingCost(shippingTotal > 0 ? shippingTotal.toFixed(2) : '');
    setManualEditDiscountCost(discountTotal > 0 ? discountTotal.toFixed(2) : '');
    setManualEditSearch('');
    setManualEditProducts([]);
    setManualEditError('');
    setManualEditVisible(true);
    await loadManualEditProducts();
  };

  const addManualEditProduct = (product: ProductSummary) => {
    setManualEditItems((current) => {
      const existing = current.find((item) => item.productId === product.id);
      if (existing) {
        return current.map((item) => item.productId === product.id ? { ...item, quantity: item.quantity + 1 } : item);
      }
      return [...current, { productId: product.id, name: product.name, sku: product.sku, quantity: 1, unitPrice: productUnitPrice(product) }];
    });
  };

  const updateManualEditQuantity = (productId: number, value: string) => {
    const quantity = Math.max(1, Number.parseInt(value.replace(/\D/g, ''), 10) || 1);
    setManualEditItems((current) => current.map((item) => item.productId === productId ? { ...item, quantity } : item));
  };

  const removeManualEditItem = (productId: number) => {
    setManualEditItems((current) => current.filter((item) => item.productId !== productId));
  };

  const submitManualOrderEdit = async () => {
    if (!order || manualEditSaving || !manualEditItems.length) return;
    setManualEditSaving(true);
    setManualEditError('');
    const shippingAmount = manualEditShippingMode === 'charged'
      ? Number.parseFloat(manualEditShippingCost.replace(/[^0-9.]/g, '')) || 0
      : 0;
    const discountAmount = Number.parseFloat(manualEditDiscountCost.replace(/[^0-9.]/g, '')) || 0;
    try {
      const response = await runAuthenticated((token) => api.updateManualOrder(
        token,
        order.id,
        {
          expected_version: order.version,
          items: manualEditItems.map((item) => ({ product_id: item.productId, quantity: item.quantity })),
          shipping_mode: manualEditShippingMode,
          shipping_base_amount: shippingAmount.toFixed(2),
          discount_amount: discountAmount.toFixed(2),
        },
        newIdempotencyKey(),
      ));
      setOrder(response.data.order);
      setManualEditVisible(false);
      setActionFeedback('Manual order updated. Copy the confirmation link when you are ready.');
      await load(true);
    } catch (reason) {
      if (reason instanceof api.ApiError && reason.status === 409) {
        setManualEditVisible(false);
        await load(true);
        Alert.alert('Order was refreshed', 'The order changed before your edit was saved. Review it and try again.');
      } else {
        setManualEditError(reason instanceof api.ApiError ? reason.message : 'Manual order could not be updated.');
      }
    } finally {
      setManualEditSaving(false);
    }
  };

  const copyConfirmationLink = async () => {
    if (!order?.confirmation_url) return;
    await Clipboard.setStringAsync(order.confirmation_url);
    Alert.alert('Link copied', 'The updated confirmation link is copied.');
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
  const shippingBaseAmount = parseMoneyAmount(order.shipping_cost?.amount);
  const shippingGstAmount = parseMoneyAmount(order.shipping_gst?.amount);
  const shippingTotalAmount = parseMoneyAmount(order.shipping_total?.amount);
  const hasShippingCharge = shippingBaseAmount > 0 || shippingGstAmount > 0 || shippingTotalAmount > 0;
  const shippingInputBaseAmount = parseMoneyAmount(shippingCost);
  const shippingInputGst = shippingInputBaseAmount * 0.18;
  const shippingInputTotal = shippingInputBaseAmount + shippingInputGst;
  const manualEditShippingAmount = manualEditShippingMode === 'charged'
    ? Number.parseFloat(manualEditShippingCost.replace(/[^0-9.]/g, '')) || 0
    : 0;
  const manualEditShippingGst = manualEditShippingAmount > 0 ? manualEditShippingAmount * 0.18 : 0;
  const manualEditShippingBase = manualEditShippingAmount > 0 ? manualEditShippingAmount - manualEditShippingGst : 0;
  const manualEditProductsTotal = manualEditItems.reduce((sum, item) => sum + item.unitPrice * item.quantity, 0);
  const manualEditDiscountAmount = Number.parseFloat(manualEditDiscountCost.replace(/[^0-9.]/g, '')) || 0;
  const manualEditTotalBeforeDiscount = manualEditProductsTotal + manualEditShippingAmount;
  const manualEditDiscountValid = manualEditDiscountAmount <= manualEditTotalBeforeDiscount;
  const manualEditReady = manualEditItems.length > 0 && (manualEditShippingMode === 'free' || manualEditShippingAmount > 0) && manualEditDiscountValid && !manualEditSaving;
  const actionFormReady = selectedAction
    ? (selectedAction.code !== 'flag_issue' || Boolean(issueReason && issueNote.trim().length >= 3))
      && (!selectedAction.required_fields.includes('customer_phone') || customerPhone.replace(/\D/g, '').length >= 10)
      && (selectedAction.target_status !== 'order_packed'
        || Boolean(
          /^[A-Za-z]{2}\d{9}[A-Za-z]{2}$/.test(trackingNumber.trim())
          && Number(packageWeightGrams) > 0
          && (Boolean(packingImageDraft) || /^https?:\/\/.+/i.test(packingImageUrl.trim()))
        ))
      && (selectedAction.target_status !== 'shipped' || Boolean(shippingCost.trim() && shippingInputBaseAmount > 0))
      && (selectedAction.target_status !== 'order_cancelled' || Boolean(cancellationReason))
    : false;
  const addressFormReady = Boolean(
    shippingAddress.name.trim()
    && shippingAddress.address_1.trim()
    && shippingAddress.city.trim()
    && shippingAddress.state.trim()
    && shippingAddress.pincode.trim().length >= 3
  );
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

  const setPickedPackingImage = (asset: ImagePicker.ImagePickerAsset | undefined) => {
    if (!asset?.base64 || !asset.uri) {
      Alert.alert('Photo not selected', 'Please choose a valid packing photo.');
      return;
    }
    const fileName = asset.fileName || `packing-${Date.now()}.jpg`;
    const mimeType = asset.mimeType || 'image/jpeg';
    setPackingImageDraft({
      uri: asset.uri,
      base64: asset.base64,
      name: fileName,
      type: mimeType,
    });
    setPackingImageUrl('');
  };

  const choosePackingImage = async () => {
    const result = await ImagePicker.launchImageLibraryAsync({
      allowsEditing: false,
      base64: true,
      mediaTypes: ['images'],
      quality: 0.72,
    });
    if (!result.canceled) setPickedPackingImage(result.assets[0]);
  };

  const takePackingImage = async () => {
    const permission = await ImagePicker.requestCameraPermissionsAsync();
    if (!permission.granted) {
      Alert.alert('Camera permission needed', 'Allow camera access to capture the packing photo.');
      return;
    }
    const result = await ImagePicker.launchCameraAsync({
      allowsEditing: false,
      base64: true,
      mediaTypes: ['images'],
      quality: 0.72,
    });
    if (!result.canceled) setPickedPackingImage(result.assets[0]);
  };
  const printShippingLabel = async () => {
    setLabelBusy(true);
    try {
      await Print.printAsync({ html: shippingLabelHtml(order) });
    } catch {
      Alert.alert('Printing unavailable', 'The Android print service could not open this label.');
    } finally {
      setLabelBusy(false);
    }
  };
  const shareShippingLabel = async () => {
    setLabelBusy(true);
    try {
      if (!(await Sharing.isAvailableAsync())) {
        Alert.alert('Sharing unavailable', 'PDF sharing is not available on this device.');
        return;
      }
      const pdf = await Print.printToFileAsync({
        html: shippingLabelHtml(order),
        width: 288,
        height: 432,
      });
      await Sharing.shareAsync(pdf.uri, {
        dialogTitle: `Shipping label ${order.reference}`,
        mimeType: 'application/pdf',
        UTI: 'com.adobe.pdf',
      });
    } catch {
      Alert.alert('PDF unavailable', 'The shipping-label PDF could not be created.');
    } finally {
      setLabelBusy(false);
    }
  };
  const canPrintShippingLabel = ['order_accepted', 'order_packed'].includes(order.status.code);
  const labelSender = order.shipping_label?.sender || {
    name: 'Mathukai Organic',
    phone: null,
    address: null,
  };

  return (
    <View style={styles.detailScreen}>
      <ScrollView
        contentContainerStyle={styles.detailContent}
        refreshControl={<RefreshControl refreshing={refreshing} onRefresh={() => void load(true)} colors={['#0B5D3B']} tintColor="#0B5D3B" />}
        stickyHeaderIndices={[0]}
      >
      <Pressable disabled={submittingAction} onPress={onBack} style={styles.backButton}>
        <MaterialCommunityIcons color="#0B5D3B" name="arrow-left" size={21} />
        <Text style={styles.backText}>Back to orders</Text>
      </Pressable>
      {error ? <View style={styles.warning}><Text style={styles.warningText}>{error} Showing the last loaded details.</Text></View> : null}
      {actionFeedback ? (
        <View accessibilityRole="alert" style={styles.successBanner}>
          <MaterialCommunityIcons color="#147348" name="check-circle" size={21} />
          <Text style={styles.successBannerText}>{actionFeedback}</Text>
        </View>
      ) : null}

      <View style={styles.detailHero}>
        <View style={styles.orderTopRow}>
          <View style={styles.orderReferenceWrap}>
            <Text style={styles.detailReference}>{order.customer.name || 'Customer unavailable'}</Text>
            <Text style={styles.orderSource}>Order {order.reference}</Text>
          </View>
          <StatusPill order={order} />
        </View>
        <View style={styles.heroTotals}>
          <Text style={styles.heroTotal}>{money(order.total)}</Text>
          <Text style={styles.paymentText}>Payment: {order.payment_state.label}</Text>
        </View>
      </View>

      {order.issue_flag ? (
        <View style={styles.issueBanner}>
          <MaterialCommunityIcons color="#B77900" name="alert-circle-outline" size={22} />
          <View style={styles.issueBannerCopy}>
            <Text style={styles.issueBannerTitle}>{order.issue_flag.reason_label}</Text>
            {order.issue_flag.note ? <Text style={styles.issueBannerText}>{order.issue_flag.note}</Text> : null}
            <Text style={styles.issueBannerMeta}>
              {[
                order.issue_flag.actor_display_name ? `Flagged by ${order.issue_flag.actor_display_name}` : '',
                order.issue_flag.created_at ? dateTime(order.issue_flag.created_at) : '',
              ].filter(Boolean).join(' · ') || 'Needs owner attention'}
            </Text>
          </View>
        </View>
      ) : null}

      {(order.can_edit_manual_order || order.confirmation_url) ? (
        <>
          <Text style={styles.sectionTitle}>Manual order tools</Text>
          <View style={styles.sectionCard}>
            <Text style={styles.actionHelp}>Update the Waiting order if needed, then copy the latest customer confirmation link manually.</Text>
            <View style={styles.actionList}>
              {order.can_edit_manual_order ? (
                <Pressable onPress={() => void openManualOrderEditor()} style={({ pressed }) => [styles.actionButton, pressed && styles.pressed]}>
                  <MaterialCommunityIcons color="#0B5D3B" name="pencil-outline" size={21} />
                  <Text style={styles.actionButtonText}>Edit manual order</Text>
                </Pressable>
              ) : null}
              {order.confirmation_url ? (
                <Pressable onPress={() => void copyConfirmationLink()} style={({ pressed }) => [styles.actionButton, pressed && styles.pressed]}>
                  <MaterialCommunityIcons color="#0B5D3B" name="content-copy" size={21} />
                  <Text style={styles.actionButtonText}>Copy confirmation link</Text>
                </Pressable>
              ) : null}
            </View>
          </View>
        </>
      ) : null}

      {order.allowed_actions.length ? (
        <>
          <Text style={styles.sectionTitle}>Order actions</Text>
          <View style={styles.sectionCard}>
            <Text style={styles.actionHelp}>Only actions currently allowed for this order are shown.</Text>
            <View style={styles.actionList}>
              {order.allowed_actions.map((action) => {
                const destructive = action.target_status === 'order_cancelled';
                const issueAction = action.code === 'flag_issue';
                return (
                  <Pressable
                    disabled={submittingAction}
                    key={`${action.code}-${action.target_status || 'custom'}`}
                    onPress={() => openAction(action)}
                    style={({ pressed }) => [
                      styles.actionButton,
                      destructive && styles.destructiveActionButton,
                      issueAction && styles.issueActionButton,
                      (pressed || submittingAction) && styles.pressed,
                    ]}
                  >
                    <MaterialCommunityIcons
                      color={destructive ? '#B42318' : issueAction ? '#B77900' : '#0B5D3B'}
                      name={action.code === 'mark_payment_received' ? 'cash-check' : issueAction ? 'alert-circle-outline' : destructive ? 'close-circle-outline' : 'arrow-right-circle-outline'}
                      size={21}
                    />
                    <Text style={[styles.actionButtonText, destructive && styles.destructiveActionText, issueAction && styles.issueActionText]}>{actionLabel(action)}</Text>
                  </Pressable>
                );
              })}
            </View>
          </View>
        </>
      ) : null}

      <View style={styles.sectionTitleRow}>
        <Text style={[styles.sectionTitle, styles.sectionTitleInRow]}>Customer</Text>
        {order.can_edit_shipping_address ? (
          <Pressable onPress={openAddressEditor} style={({ pressed }) => [styles.editAddressButton, pressed && styles.pressed]}>
            <MaterialCommunityIcons color="#0B5D3B" name="pencil-outline" size={17} />
            <Text style={styles.editAddressText}>Edit details</Text>
          </Pressable>
        ) : null}
      </View>
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

      <Modal
        animationType="slide"
        onRequestClose={() => !addressSaving && setAddressEditorVisible(false)}
        transparent
        visible={addressEditorVisible}
      >
        <KeyboardAvoidingView behavior={Platform.OS === 'ios' ? 'padding' : 'height'} style={styles.modalKeyboardView}>
          <View style={styles.modalBackdrop}>
            <View style={styles.addressModal}>
              <View style={styles.modalHeader}>
                <View style={styles.addressModalTitleWrap}>
                  <Text style={styles.modalTitle}>Edit delivery details</Text>
                  <Text style={styles.addressModalHint}>The name and address will be used on the shipping label.</Text>
                </View>
                <Pressable disabled={addressSaving} onPress={() => setAddressEditorVisible(false)} style={styles.modalClose}>
                  <MaterialCommunityIcons color="#587066" name="close" size={24} />
                </Pressable>
              </View>
              <ScrollView keyboardShouldPersistTaps="handled" showsVerticalScrollIndicator={false}>
                <View style={styles.formGroup}>
                  <Text style={styles.formLabel}>Customer name *</Text>
                  <TextInput autoCapitalize="words" maxLength={160} onChangeText={(value) => updateAddressField('name', value)} placeholder="Customer name" placeholderTextColor="#82958D" style={styles.formInput} value={shippingAddress.name} />
                </View>
                <View style={styles.formGroup}>
                  <Text style={styles.formLabel}>Address line 1 *</Text>
                  <TextInput maxLength={255} onChangeText={(value) => updateAddressField('address_1', value)} placeholder="House number and street" placeholderTextColor="#82958D" style={styles.formInput} value={shippingAddress.address_1} />
                </View>
                <View style={styles.formGroup}>
                  <Text style={styles.formLabel}>Address line 2</Text>
                  <TextInput maxLength={255} onChangeText={(value) => updateAddressField('address_2', value)} placeholder="Area or landmark (optional)" placeholderTextColor="#82958D" style={styles.formInput} value={shippingAddress.address_2} />
                </View>
                <View style={styles.addressFieldRow}>
                  <View style={styles.addressFieldHalf}>
                    <Text style={styles.formLabel}>City *</Text>
                    <TextInput maxLength={120} onChangeText={(value) => updateAddressField('city', value)} placeholder="City" placeholderTextColor="#82958D" style={styles.formInput} value={shippingAddress.city} />
                  </View>
                  <View style={styles.addressFieldHalf}>
                    <Text style={styles.formLabel}>State *</Text>
                    <TextInput maxLength={120} onChangeText={(value) => updateAddressField('state', value)} placeholder="State" placeholderTextColor="#82958D" style={styles.formInput} value={shippingAddress.state} />
                  </View>
                </View>
                <View style={styles.addressFieldRow}>
                  <View style={styles.addressFieldHalf}>
                    <Text style={styles.formLabel}>Pincode *</Text>
                    <TextInput keyboardType="number-pad" maxLength={20} onChangeText={(value) => updateAddressField('pincode', value)} placeholder="Pincode" placeholderTextColor="#82958D" style={styles.formInput} value={shippingAddress.pincode} />
                  </View>
                  <View style={styles.addressFieldHalf}>
                    <Text style={styles.formLabel}>Country</Text>
                    <TextInput maxLength={120} onChangeText={(value) => updateAddressField('country', value)} placeholder="India" placeholderTextColor="#82958D" style={styles.formInput} value={shippingAddress.country} />
                  </View>
                </View>
                {addressError ? <Text accessibilityRole="alert" style={styles.addressError}>{addressError}</Text> : null}
              </ScrollView>
              <Pressable
                disabled={!addressFormReady || addressSaving}
                onPress={() => void submitShippingAddress()}
                style={[styles.confirmActionButton, (!addressFormReady || addressSaving) && styles.disabledButton]}
              >
                {addressSaving ? <ActivityIndicator color="#FFFFFF" /> : <Text style={styles.confirmActionText}>Save delivery details</Text>}
              </Pressable>
            </View>
          </View>
        </KeyboardAvoidingView>
      </Modal>

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
        <DetailRow label="Package weight" value={order.package_weight_grams ? `${order.package_weight_grams} g` : null} />
        <DetailRow label="Packing image" value={order.packing_image_url} />
        {order.packing_image_url ? (
          <Image source={{ uri: order.packing_image_url }} style={styles.packingPreviewImage} />
        ) : null}
        {hasShippingCharge ? (
          <>
            <DetailRow label="Shipping charge" value={money(order.shipping_cost)} />
            <DetailRow label="GST charge" value={money(order.shipping_gst)} />
            <DetailRow label="Total shipping charge" value={money(order.shipping_total)} />
          </>
        ) : (
          <DetailRow label="Shipping charges" value="Free" />
        )}
        <DetailRow label="Order date" value={dateTime(order.order_date)} />
        {canPrintShippingLabel ? (
          <Pressable
            onPress={() => setLabelPreviewVisible(true)}
            style={({ pressed }) => [styles.labelButton, pressed && styles.pressed]}
          >
            <MaterialCommunityIcons color="#FFFFFF" name="printer-outline" size={21} />
            <Text style={styles.labelButtonText}>View and print shipping label</Text>
          </Pressable>
        ) : null}
      </View>

      <Modal animationType="slide" onRequestClose={closeShippingLabel} visible={labelPreviewVisible}>
        <View style={styles.labelPreviewScreen}>
          <View style={styles.labelPreviewHeader}>
            <View style={styles.labelPreviewHeaderCopy}>
              <Text style={styles.labelPreviewTitle}>Shipping label</Text>
              <Text style={styles.labelPreviewSubtitle}>4 × 6 inches · {order.reference}</Text>
            </View>
          </View>
          <ScrollView contentContainerStyle={styles.labelPreviewScroll}>
            <View style={styles.labelSheet}>
              <Text style={styles.labelBrand}>MATHUKAI ORGANIC</Text>
              <Text style={styles.labelSheetTitle}>SHIPPING LABEL</Text>
              <Text style={styles.labelSheetReference}>Order {order.reference}</Text>
              {order.courier_name ? <Text style={styles.labelSheetMeta}>Courier: {order.courier_name}</Text> : null}
              {order.tracking_number ? <Text style={styles.labelSheetMeta}>Tracking: {order.tracking_number}</Text> : null}
              <Text style={styles.labelSheetMeta}>India Post Customer ID: {INDIA_POST_CUSTOMER_ID}</Text>
              {order.package_weight_grams ? <Text style={styles.labelSheetMeta}>Weight: {order.package_weight_grams} g</Text> : null}
              <View style={styles.labelAddressBox}>
                <Text style={styles.labelEyebrow}>TO</Text>
                <Text style={styles.labelRecipient}>{order.customer.name || 'Customer'}</Text>
                <Text style={styles.labelAddress}>{order.customer.delivery_address || 'Delivery address unavailable'}</Text>
                {order.customer.phone ? <Text style={styles.labelPhone}>Phone: {order.customer.phone}</Text> : null}
              </View>
              <View style={styles.labelAddressBox}>
                <Text style={styles.labelEyebrow}>FROM</Text>
                <Text style={styles.labelSender}>{labelSender.name}</Text>
                <Text style={styles.labelAddress}>{labelSender.address || 'Sender address unavailable'}</Text>
                {labelSender.phone ? <Text style={styles.labelPhone}>Phone: {labelSender.phone}</Text> : null}
              </View>
            </View>
          </ScrollView>
          <View style={[styles.labelActions, { paddingBottom: Math.max(insets.bottom + 10, 18) }]}>
            <Pressable
              accessibilityLabel="Back to order details"
              accessibilityRole="button"
              onPress={closeShippingLabel}
              style={({ pressed }) => [styles.labelFooterBackButton, pressed && styles.pressed]}
            >
              <MaterialCommunityIcons color="#29483D" name="arrow-left" size={21} />
              <Text style={styles.labelFooterBackText}>Back</Text>
            </Pressable>
            <Pressable disabled={labelBusy} onPress={() => void shareShippingLabel()} style={styles.shareLabelButton}>
              <MaterialCommunityIcons color="#0B5D3B" name="share-variant-outline" size={21} />
              <Text style={styles.shareLabelText}>Share PDF</Text>
            </Pressable>
            <Pressable disabled={labelBusy} onPress={() => void printShippingLabel()} style={styles.printLabelButton}>
              {labelBusy ? <ActivityIndicator color="#FFFFFF" /> : <MaterialCommunityIcons color="#FFFFFF" name="printer" size={21} />}
              <Text style={styles.printLabelText}>Print</Text>
            </Pressable>
          </View>
        </View>
      </Modal>

      <Modal
        animationType="slide"
        onRequestClose={() => !manualEditSaving && setManualEditVisible(false)}
        transparent
        visible={manualEditVisible}
      >
        <KeyboardAvoidingView behavior={Platform.OS === 'ios' ? 'padding' : 'height'} style={styles.modalKeyboardView}>
          <View style={styles.modalBackdrop}>
            <View style={styles.actionModal}>
              <View style={styles.modalHeader}>
                <Text style={styles.modalTitle}>Edit manual order</Text>
                <Pressable disabled={manualEditSaving} onPress={() => setManualEditVisible(false)} style={styles.modalClose}>
                  <MaterialCommunityIcons color="#587066" name="close" size={24} />
                </Pressable>
              </View>
              <ScrollView contentContainerStyle={styles.actionModalScroll} keyboardDismissMode="on-drag" keyboardShouldPersistTaps="handled">
                <Text style={styles.formLabel}>Products</Text>
                {manualEditItems.length ? manualEditItems.map((item) => (
                  <View key={item.productId} style={styles.manualEditItemRow}>
                    <View style={styles.manualEditItemCopy}>
                      <Text numberOfLines={1} style={styles.itemName}>{item.name}</Text>
                      <Text style={styles.itemMeta}>{formatInrAmount(item.unitPrice)} each{item.sku ? ` · SKU ${item.sku}` : ''}</Text>
                    </View>
                    <TextInput
                      keyboardType="number-pad"
                      onChangeText={(value) => updateManualEditQuantity(item.productId, value)}
                      style={styles.manualEditQuantityInput}
                      value={String(item.quantity)}
                    />
                    <Pressable onPress={() => removeManualEditItem(item.productId)} style={styles.manualEditRemoveButton}>
                      <MaterialCommunityIcons color="#B42318" name="trash-can-outline" size={19} />
                    </Pressable>
                  </View>
                )) : <Text style={styles.emptyText}>Add at least one product.</Text>}

                <View style={styles.searchRow}>
                  <TextInput
                    autoCapitalize="none"
                    onChangeText={setManualEditSearch}
                    onSubmitEditing={() => void loadManualEditProducts()}
                    placeholder="Search product to add"
                    placeholderTextColor="#82958D"
                    returnKeyType="search"
                    style={styles.searchInput}
                    value={manualEditSearch}
                  />
                  <Pressable onPress={() => void loadManualEditProducts()} style={styles.searchButton}>
                    <MaterialCommunityIcons color="#FFFFFF" name="magnify" size={22} />
                  </Pressable>
                </View>
                {manualEditLoading ? <ActivityIndicator color="#0B5D3B" /> : manualEditProducts.slice(0, 8).map((product) => (
                  <Pressable key={product.id} onPress={() => addManualEditProduct(product)} style={({ pressed }) => [styles.manualEditProductRow, pressed && styles.pressed]}>
                    <View style={styles.manualEditItemCopy}>
                      <Text numberOfLines={1} style={styles.itemName}>{product.name}</Text>
                      <Text style={styles.itemMeta}>{formatInrAmount(productUnitPrice(product))} each · Stock {product.stock_quantity}</Text>
                    </View>
                    <MaterialCommunityIcons color="#0B5D3B" name="plus-circle-outline" size={22} />
                  </Pressable>
                ))}

                <Text style={styles.formLabel}>Shipping</Text>
                <View style={styles.reasonGrid}>
                  <Pressable onPress={() => setManualEditShippingMode('free')} style={[styles.reasonChip, manualEditShippingMode === 'free' && styles.reasonChipActive]}>
                    <Text style={[styles.reasonChipText, manualEditShippingMode === 'free' && styles.reasonChipTextActive]}>Free</Text>
                  </Pressable>
                  <Pressable onPress={() => setManualEditShippingMode('charged')} style={[styles.reasonChip, manualEditShippingMode === 'charged' && styles.reasonChipActive]}>
                    <Text style={[styles.reasonChipText, manualEditShippingMode === 'charged' && styles.reasonChipTextActive]}>Charged</Text>
                  </Pressable>
                </View>
                {manualEditShippingMode === 'charged' ? (
                  <View style={styles.formGroup}>
                    <Text style={styles.formLabel}>Total shipping amount including GST</Text>
                    <TextInput keyboardType="decimal-pad" onChangeText={setManualEditShippingCost} placeholder="0.00" placeholderTextColor="#82958D" style={styles.formInput} value={manualEditShippingCost} />
                    <Text style={styles.formHint}>
                      Shipping {formatInrAmount(manualEditShippingBase)} + GST {formatInrAmount(manualEditShippingGst)} = {formatInrAmount(manualEditShippingAmount)}
                    </Text>
                  </View>
                ) : (
                  <Text style={styles.formHint}>Shipping will show as Free.</Text>
                )}
                <View style={styles.formGroup}>
                  <Text style={styles.formLabel}>Discount amount</Text>
                  <TextInput keyboardType="decimal-pad" onChangeText={setManualEditDiscountCost} placeholder="0.00" placeholderTextColor="#82958D" style={styles.formInput} value={manualEditDiscountCost} />
                  <Text style={[styles.formHint, !manualEditDiscountValid && styles.addressError]}>
                    {manualEditDiscountValid
                      ? `Customer will see discount applied: -${formatInrAmount(manualEditDiscountAmount)}`
                      : 'Discount cannot be more than order total.'}
                  </Text>
                </View>
                <View style={styles.manualEditTotalBox}>
                  <Text style={styles.shippingTaxTotalLabel}>Updated total</Text>
                  <Text style={styles.shippingTaxTotalValue}>{formatInrAmount(Math.max(0, manualEditTotalBeforeDiscount - manualEditDiscountAmount))}</Text>
                </View>
                {manualEditError ? <Text accessibilityRole="alert" style={styles.addressError}>{manualEditError}</Text> : null}
              </ScrollView>
              <Pressable disabled={!manualEditReady} onPress={() => void submitManualOrderEdit()} style={[styles.confirmActionButton, !manualEditReady && styles.disabledButton]}>
                {manualEditSaving ? <ActivityIndicator color="#FFFFFF" /> : <Text style={styles.confirmActionText}>Save updated order</Text>}
              </Pressable>
            </View>
          </View>
        </KeyboardAvoidingView>
      </Modal>

      <Modal
        animationType="slide"
        onRequestClose={() => !submittingAction && setSelectedAction(null)}
        transparent
        visible={selectedAction !== null}
      >
        <KeyboardAvoidingView behavior={Platform.OS === 'ios' ? 'padding' : 'height'} style={styles.modalKeyboardView}>
          <View style={styles.modalBackdrop}>
            <View style={styles.actionModal}>
            <View style={styles.modalHeader}>
              <Text style={styles.modalTitle}>{selectedAction ? actionLabel(selectedAction) : ''}</Text>
              <Pressable disabled={submittingAction} onPress={() => setSelectedAction(null)} style={styles.modalClose}>
                <MaterialCommunityIcons color="#587066" name="close" size={24} />
              </Pressable>
            </View>
            <ScrollView
              contentContainerStyle={styles.actionModalScroll}
              keyboardDismissMode="on-drag"
              keyboardShouldPersistTaps="handled"
              showsVerticalScrollIndicator={false}
            >
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
              {selectedAction?.target_status === 'order_packed' ? (
                <>
                  <View style={styles.formGroup}>
                    <Text style={styles.formLabel}>Tracking number</Text>
                    <View style={styles.trackingInputRow}>
                      <TextInput
                        autoCapitalize="characters"
                        maxLength={13}
                        onChangeText={(value) => setTrackingNumber(normalizeTrackingBarcode(value))}
                        placeholder="AA123456789AA"
                        placeholderTextColor="#82958D"
                        style={[styles.formInput, styles.trackingInput]}
                        value={trackingNumber}
                      />
                      <Pressable onPress={() => void openTrackingScanner()} style={({ pressed }) => [styles.scanTrackingButton, pressed && styles.pressed]}>
                        <MaterialCommunityIcons color="#FFFFFF" name="barcode-scan" size={22} />
                        <Text style={styles.scanTrackingText}>Scan</Text>
                      </Pressable>
                    </View>
                    <Text style={styles.formHint}>Scan the India Post barcode or type 2 letters, 9 digits, then 2 letters.</Text>
                  </View>
                  <View style={styles.formGroup}>
                    <Text style={styles.formLabel}>Package weight (g)</Text>
                    <TextInput
                      keyboardType="number-pad"
                      maxLength={6}
                      onChangeText={setPackageWeightGrams}
                      placeholder="Example: 250"
                      placeholderTextColor="#82958D"
                      style={styles.formInput}
                      value={packageWeightGrams}
                    />
                    <Text style={styles.formHint}>Enter the packed shipment weight in grams.</Text>
                  </View>
                  <View style={styles.formGroup}>
                    <Text style={styles.formLabel}>Packing image</Text>
                    <View style={styles.photoActionRow}>
                      <Pressable onPress={() => void takePackingImage()} style={styles.photoActionButton}>
                        <MaterialCommunityIcons color="#0B5D3B" name="camera-outline" size={20} />
                        <Text style={styles.photoActionText}>Take photo</Text>
                      </Pressable>
                      <Pressable onPress={() => void choosePackingImage()} style={styles.photoActionButton}>
                        <MaterialCommunityIcons color="#0B5D3B" name="image-outline" size={20} />
                        <Text style={styles.photoActionText}>Choose photo</Text>
                      </Pressable>
                    </View>
                    {packingImageDraft ? (
                      <Image source={{ uri: packingImageDraft.uri }} style={styles.packingPreviewImage} />
                    ) : null}
                    {order?.packing_image_url && !packingImageDraft ? (
                      <Text style={styles.formHint}>Existing packing image is already saved.</Text>
                    ) : (
                      <Text style={styles.formHint}>Upload the packed parcel photo before moving this order to Packed.</Text>
                    )}
                  </View>
                </>
              ) : null}
              {selectedAction?.target_status === 'shipped' ? (
                <>
                  <View style={styles.formGroup}>
                    <Text style={styles.formLabel}>Shipping charge before GST</Text>
                    <TextInput keyboardType="decimal-pad" onChangeText={setShippingCost} placeholder="0.00" placeholderTextColor="#82958D" style={styles.formInput} value={shippingCost} />
                    {shippingInputBaseAmount > 0 ? (
                      <View style={styles.shippingTaxPreview}>
                        <View style={styles.shippingTaxRow}>
                          <Text style={styles.shippingTaxLabel}>GST charge</Text>
                          <Text style={styles.shippingTaxValue}>{formatInrAmount(shippingInputGst)}</Text>
                        </View>
                        <View style={styles.shippingTaxRow}>
                          <Text style={styles.shippingTaxTotalLabel}>Total shipping charge</Text>
                          <Text style={styles.shippingTaxTotalValue}>{formatInrAmount(shippingInputTotal)}</Text>
                        </View>
                      </View>
                    ) : null}
                  </View>
                </>
              ) : null}
              {selectedAction?.code === 'flag_issue' ? (
                <>
                  <Text style={styles.formLabel}>Issue reason</Text>
                  <View style={styles.reasonGrid}>
                    {ISSUE_REASONS.map((reason) => (
                      <Pressable
                        key={reason.code}
                        onPress={() => setIssueReason(reason.code)}
                        style={[styles.reasonChip, issueReason === reason.code && styles.reasonChipActive]}
                      >
                        <Text style={[styles.reasonChipText, issueReason === reason.code && styles.reasonChipTextActive]}>{reason.label}</Text>
                      </Pressable>
                    ))}
                  </View>
                  <View style={styles.formGroup}>
                    <Text style={styles.formLabel}>Note *</Text>
                    <TextInput
                      maxLength={300}
                      multiline
                      onChangeText={setIssueNote}
                      placeholder="Example: Customer address is incomplete, please review before shipping."
                      placeholderTextColor="#82958D"
                      style={[styles.formInput, styles.formTextArea]}
                      value={issueNote}
                    />
                    <Text style={styles.formHint}>This will move the order to Needs attention and save the note in activity.</Text>
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
        </KeyboardAvoidingView>
      </Modal>

      <Modal animationType="slide" visible={trackingScannerVisible} onRequestClose={() => setTrackingScannerVisible(false)}>
        <View style={styles.scannerScreen}>
          <CameraView
            barcodeScannerSettings={{
              barcodeTypes: ['code128', 'code39', 'ean13', 'ean8', 'upc_a', 'upc_e', 'itf14'],
            }}
            onBarcodeScanned={trackingScanned ? undefined : handleTrackingBarcode}
            style={styles.scannerCamera}
          >
            <View style={styles.scannerOverlay}>
              <View style={styles.scannerTopBar}>
                <Text style={styles.scannerTitle}>Scan tracking barcode</Text>
                <Pressable onPress={() => setTrackingScannerVisible(false)} style={styles.scannerCloseButton}>
                  <MaterialCommunityIcons color="#FFFFFF" name="close" size={24} />
                </Pressable>
              </View>
              <View style={styles.scannerGuide}>
                <View style={styles.scannerFrame} />
                <Text style={styles.scannerHint}>Place the India Post barcode inside the box.</Text>
              </View>
            </View>
          </CameraView>
        </View>
      </Modal>

      </ScrollView>
      {submittingAction ? (
        <View accessibilityViewIsModal style={styles.actionProgressOverlay}>
          <View style={styles.actionProgressCard}>
            <ActivityIndicator color="#0B5D3B" size="large" />
            <Text style={styles.actionProgressTitle}>{actionProgressLabel}</Text>
            <Text style={styles.actionProgressHint}>Please wait. Do not tap the action again.</Text>
          </View>
        </View>
      ) : null}
    </View>
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

  const loadFirstPage = useCallback(async (refresh = false, silent = false) => {
    if (!silent) {
      if (refresh) setRefreshing(true); else setLoading(true);
      setError('');
    }
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
      if (!silent) setError(reason instanceof api.ApiError ? reason.message : 'Orders could not be loaded.');
    } finally {
      if (!silent) {
        setLoading(false);
        setRefreshing(false);
      }
    }
  }, [dateFrom, dateTo, runAuthenticated, search, status]);

  useEffect(() => { void loadFirstPage(); }, [loadFirstPage]);

  useEffect(() => {
    if (selectedOrderId !== null) return undefined;
    const refresh = () => void loadFirstPage(false, true);
    const interval = setInterval(refresh, 15000);
    const subscription = AppState.addEventListener('change', (state) => {
      if (state === 'active') refresh();
    });
    return () => {
      clearInterval(interval);
      subscription.remove();
    };
  }, [loadFirstPage, selectedOrderId]);

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
        <Pressable accessibilityLabel="Search" onPress={() => setSearch(draftSearch.trim())} style={styles.searchButton}>
          <MaterialCommunityIcons color="#FFFFFF" name="magnify" size={23} />
        </Pressable>
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
  listContent: { padding: 16, paddingBottom: 28 },
  scopeBanner: { backgroundColor: '#E4F3EB', borderRadius: 12, marginBottom: 12, paddingHorizontal: 14, paddingVertical: 10 },
  scopeLabel: { color: '#0B5D3B', fontSize: 13, fontWeight: '800' },
  scopeDates: { color: '#587066', fontSize: 11, marginTop: 2 },
  searchRow: { flexDirection: 'row', marginBottom: 13 },
  searchInput: { flex: 1, minHeight: 50, backgroundColor: '#FFFFFF', borderColor: '#CBD9D3', borderWidth: 1, borderRadius: 14, paddingHorizontal: 15, color: '#17352A', fontSize: 15 },
  searchButton: { width: 50, height: 50, backgroundColor: '#0B5D3B', borderRadius: 14, alignItems: 'center', justifyContent: 'center', marginLeft: 8 },
  searchButtonText: { color: '#FFFFFF', fontWeight: '800' },
  filterRow: { paddingBottom: 12, columnGap: 8 },
  filterChip: { borderColor: '#CBD9D3', borderWidth: 1, borderRadius: 20, backgroundColor: '#FFFFFF', paddingHorizontal: 14, paddingVertical: 9 },
  filterChipActive: { backgroundColor: '#0B5D3B', borderColor: '#0B5D3B' },
  filterText: { color: '#587066', fontSize: 13, fontWeight: '700' },
  filterTextActive: { color: '#FFFFFF' },
  resultLabel: { color: '#71867D', fontSize: 12, fontWeight: '700', marginBottom: 10, marginLeft: 2 },
  orderCard: { minHeight: 108, backgroundColor: '#FFFFFF', borderColor: '#DEE7E3', borderWidth: 1, borderRadius: 17, padding: 12, marginBottom: 11, flexDirection: 'row', alignItems: 'center', shadowColor: '#17352A', shadowOffset: { width: 0, height: 2 }, shadowOpacity: 0.05, shadowRadius: 6, elevation: 1 },
  orderCardBody: { flex: 1 },
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
  orderBottomRow: { flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center', marginTop: 9 },
  orderMetaRow: { flexDirection: 'row', alignItems: 'center', columnGap: 8 },
  orderMeta: { color: '#71867D', fontSize: 12 },
  trackingRow: { flexDirection: 'row', alignItems: 'center', marginTop: 7, columnGap: 5 },
  trackingText: { color: '#1769C2', fontSize: 11, fontWeight: '800', flex: 1 },
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
  detailScreen: { flex: 1, backgroundColor: '#F7FAF8' },
  detailContent: { padding: 16, paddingBottom: 32 },
  backButton: { minHeight: 48, backgroundColor: '#FFFFFF', borderColor: '#DCE5E1', borderWidth: 1, borderRadius: 13, paddingHorizontal: 14, flexDirection: 'row', alignItems: 'center', marginBottom: 10, columnGap: 8 },
  backText: { color: '#0B5D3B', fontSize: 15, fontWeight: '800' },
  successBanner: { backgroundColor: '#E4F3EB', borderColor: '#B8D5C8', borderWidth: 1, borderRadius: 12, padding: 12, marginBottom: 12, flexDirection: 'row', alignItems: 'center', columnGap: 9 },
  successBannerText: { color: '#147348', flex: 1, fontWeight: '800' },
  actionProgressOverlay: { ...StyleSheet.absoluteFill, backgroundColor: 'rgba(15, 35, 28, 0.38)', alignItems: 'center', justifyContent: 'center', padding: 30, zIndex: 20 },
  actionProgressCard: { width: '100%', maxWidth: 340, backgroundColor: '#FFFFFF', borderRadius: 18, padding: 24, alignItems: 'center' },
  actionProgressTitle: { color: '#17352A', fontSize: 17, fontWeight: '900', marginTop: 14, textAlign: 'center' },
  actionProgressHint: { color: '#71867D', fontSize: 12, lineHeight: 18, marginTop: 6, textAlign: 'center' },
  detailHero: { backgroundColor: '#FFFFFF', borderColor: '#DEE7E3', borderWidth: 1, borderRadius: 18, padding: 18, marginBottom: 22, shadowColor: '#17352A', shadowOffset: { width: 0, height: 2 }, shadowOpacity: 0.05, shadowRadius: 6, elevation: 1 },
  detailReference: { color: '#17352A', fontSize: 21, fontWeight: '900' },
  heroTotals: { borderTopColor: '#E4EAE7', borderTopWidth: 1, marginTop: 18, paddingTop: 14, flexDirection: 'row', alignItems: 'flex-end', justifyContent: 'space-between' },
  heroTotal: { color: '#17352A', fontSize: 24, fontWeight: '900' },
  paymentText: { color: '#587066', fontSize: 12, fontWeight: '700' },
  issueBanner: { backgroundColor: '#FFF8E7', borderColor: '#F0D08D', borderWidth: 1, borderRadius: 16, padding: 14, marginTop: -10, marginBottom: 22, flexDirection: 'row', columnGap: 10 },
  issueBannerCopy: { flex: 1 },
  issueBannerTitle: { color: '#7A4A00', fontSize: 15, fontWeight: '900' },
  issueBannerText: { color: '#4F3B17', fontSize: 13, lineHeight: 19, marginTop: 5 },
  issueBannerMeta: { color: '#8A6A2A', fontSize: 11, fontWeight: '700', marginTop: 7 },
  sectionTitle: { color: '#17352A', fontSize: 18, fontWeight: '800', marginBottom: 10 },
  sectionTitleRow: { minHeight: 38, flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', marginBottom: 10 },
  sectionTitleInRow: { marginBottom: 0 },
  editAddressButton: { minHeight: 36, borderColor: '#B8D5C8', borderWidth: 1, borderRadius: 18, paddingHorizontal: 12, flexDirection: 'row', alignItems: 'center', columnGap: 6, backgroundColor: '#F4FAF7' },
  editAddressText: { color: '#0B5D3B', fontSize: 12, fontWeight: '800' },
  sectionCard: { backgroundColor: '#FFFFFF', borderColor: '#E0E7E3', borderWidth: 1, borderRadius: 17, padding: 16, marginBottom: 22 },
  actionHelp: { color: '#71867D', fontSize: 12, lineHeight: 18, marginBottom: 12 },
  actionList: { flexDirection: 'row', flexWrap: 'wrap', gap: 9 },
  actionButton: { flexBasis: '48%', flexGrow: 1, minHeight: 50, borderColor: '#B8D5C8', borderWidth: 1, borderRadius: 13, backgroundColor: '#F4FAF7', paddingHorizontal: 12, flexDirection: 'row', alignItems: 'center', justifyContent: 'center', columnGap: 8 },
  actionButtonText: { color: '#0B5D3B', fontSize: 14, fontWeight: '800' },
  destructiveActionButton: { backgroundColor: '#FFF6F5', borderColor: '#F1C0BC' },
  destructiveActionText: { color: '#B42318' },
  issueActionButton: { backgroundColor: '#FFF8E7', borderColor: '#F0D08D' },
  issueActionText: { color: '#9A5B00' },
  detailRow: { marginBottom: 13 },
  detailLabel: { color: '#71867D', fontSize: 11, fontWeight: '800', letterSpacing: 0.6, textTransform: 'uppercase' },
  detailValue: { color: '#29483D', fontSize: 15, lineHeight: 21, fontWeight: '600', marginTop: 4 },
  packingPreviewImage: { width: '100%', height: 180, borderRadius: 14, backgroundColor: '#EEF3F0', marginTop: 8, marginBottom: 12 },
  maskedNote: { color: '#7A4A00', backgroundColor: '#FFF4D8', borderRadius: 9, padding: 10, lineHeight: 18 },
  contactActions: { borderTopColor: '#E7ECEA', borderTopWidth: 1, flexDirection: 'row', columnGap: 10, marginTop: 4, paddingTop: 14 },
  whatsAppButton: { flex: 1, minHeight: 48, backgroundColor: '#128C4A', borderRadius: 13, flexDirection: 'row', alignItems: 'center', justifyContent: 'center', columnGap: 8 },
  whatsAppButtonText: { color: '#FFFFFF', fontWeight: '800' },
  callButton: { flex: 1, minHeight: 48, backgroundColor: '#FFFFFF', borderColor: '#0B5D3B', borderWidth: 1, borderRadius: 13, flexDirection: 'row', alignItems: 'center', justifyContent: 'center', columnGap: 8 },
  callButtonText: { color: '#0B5D3B', fontWeight: '800' },
  labelButton: { minHeight: 50, backgroundColor: '#0B5D3B', borderRadius: 13, flexDirection: 'row', alignItems: 'center', justifyContent: 'center', columnGap: 9, marginTop: 5 },
  labelButtonText: { color: '#FFFFFF', fontWeight: '900' },
  labelPreviewScreen: { flex: 1, backgroundColor: '#EEF3F0' },
  labelPreviewHeader: { minHeight: 76, backgroundColor: '#FFFFFF', borderBottomColor: '#DCE5E1', borderBottomWidth: 1, paddingHorizontal: 18, flexDirection: 'row', alignItems: 'center', columnGap: 12 },
  labelPreviewHeaderCopy: { flex: 1 },
  labelPreviewTitle: { color: '#17352A', fontSize: 20, fontWeight: '900' },
  labelPreviewSubtitle: { color: '#71867D', fontSize: 12, marginTop: 3 },
  labelPreviewScroll: { padding: 18, alignItems: 'center' },
  labelSheet: { width: '100%', maxWidth: 390, minHeight: 560, backgroundColor: '#FFFFFF', borderColor: '#17211D', borderWidth: 2, padding: 16 },
  labelBrand: { color: '#111111', fontSize: 14, fontWeight: '900', letterSpacing: 0.8, marginBottom: 5 },
  labelSheetTitle: { color: '#111111', fontSize: 23, fontWeight: '900' },
  labelSheetReference: { color: '#111111', fontSize: 16, fontWeight: '900', borderBottomColor: '#111111', borderBottomWidth: 2, paddingBottom: 12, marginTop: 5, marginBottom: 6 },
  labelSheetMeta: { color: '#111111', fontSize: 12, fontWeight: '800', marginTop: 3 },
  labelAddressBox: { borderColor: '#111111', borderWidth: 1.5, padding: 13, marginTop: 14 },
  labelEyebrow: { color: '#111111', fontSize: 10, fontWeight: '900', marginBottom: 7 },
  labelRecipient: { color: '#111111', fontSize: 20, fontWeight: '900', marginBottom: 6 },
  labelSender: { color: '#111111', fontSize: 16, fontWeight: '900', marginBottom: 6 },
  labelAddress: { color: '#111111', fontSize: 14, lineHeight: 20 },
  labelPhone: { color: '#111111', fontSize: 13, fontWeight: '900', marginTop: 8 },
  labelActions: { backgroundColor: '#FFFFFF', borderTopColor: '#DCE5E1', borderTopWidth: 1, padding: 10, flexDirection: 'row', columnGap: 7 },
  labelFooterBackButton: { flex: 0.8, minHeight: 52, borderColor: '#B9C8C2', borderWidth: 1, borderRadius: 13, flexDirection: 'row', alignItems: 'center', justifyContent: 'center', columnGap: 5 },
  labelFooterBackText: { color: '#29483D', fontSize: 13, fontWeight: '900' },
  shareLabelButton: { flex: 1, minHeight: 52, borderColor: '#0B5D3B', borderWidth: 1, borderRadius: 13, flexDirection: 'row', alignItems: 'center', justifyContent: 'center', columnGap: 8 },
  shareLabelText: { color: '#0B5D3B', fontSize: 13, fontWeight: '900' },
  printLabelButton: { flex: 1, minHeight: 52, backgroundColor: '#0B5D3B', borderRadius: 13, flexDirection: 'row', alignItems: 'center', justifyContent: 'center', columnGap: 8 },
  printLabelText: { color: '#FFFFFF', fontSize: 13, fontWeight: '900' },
  itemRow: { flexDirection: 'row', alignItems: 'center', paddingVertical: 6 },
  itemDivider: { borderTopColor: '#E7ECEA', borderTopWidth: 1, paddingTop: 13, marginTop: 7 },
  itemCopy: { flex: 1, paddingRight: 12 },
  itemName: { color: '#29483D', fontSize: 15, fontWeight: '800' },
  itemMeta: { color: '#71867D', fontSize: 12, marginTop: 4 },
  itemTotal: { color: '#17352A', fontWeight: '900' },
  manualEditItemRow: { minHeight: 62, borderColor: '#E0E8E4', borderWidth: 1, borderRadius: 13, padding: 10, marginBottom: 9, flexDirection: 'row', alignItems: 'center', columnGap: 8 },
  manualEditProductRow: { minHeight: 58, borderBottomColor: '#E7ECEA', borderBottomWidth: 1, paddingVertical: 9, flexDirection: 'row', alignItems: 'center', columnGap: 8 },
  manualEditItemCopy: { flex: 1 },
  manualEditQuantityInput: { width: 58, minHeight: 42, borderColor: '#CBD9D3', borderWidth: 1, borderRadius: 10, color: '#17352A', fontSize: 15, fontWeight: '800', textAlign: 'center' },
  manualEditRemoveButton: { width: 38, height: 38, borderRadius: 19, backgroundColor: '#FFF1F0', alignItems: 'center', justifyContent: 'center' },
  manualEditTotalBox: { backgroundColor: '#F4FAF7', borderColor: '#DCE9E3', borderWidth: 1, borderRadius: 12, padding: 12, marginBottom: 14, flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between' },
  modalBackdrop: { flex: 1, backgroundColor: 'rgba(15, 35, 28, 0.52)', justifyContent: 'flex-end' },
  modalKeyboardView: { flex: 1 },
  actionModal: { maxHeight: '88%', backgroundColor: '#FFFFFF', borderTopLeftRadius: 24, borderTopRightRadius: 24, paddingHorizontal: 20, paddingTop: 18, paddingBottom: 22 },
  actionModalScroll: { paddingBottom: 18 },
  addressModal: { maxHeight: '92%', backgroundColor: '#FFFFFF', borderTopLeftRadius: 24, borderTopRightRadius: 24, paddingHorizontal: 20, paddingTop: 18, paddingBottom: 22 },
  addressModalTitleWrap: { flex: 1, paddingRight: 12 },
  addressModalHint: { color: '#71867D', fontSize: 12, lineHeight: 18, marginTop: 4 },
  addressFieldRow: { flexDirection: 'row', columnGap: 10, marginBottom: 16 },
  addressFieldHalf: { flex: 1 },
  addressError: { color: '#B42318', backgroundColor: '#FFF2F0', borderRadius: 10, padding: 11, lineHeight: 18, marginBottom: 14 },
  modalHeader: { flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', marginBottom: 18 },
  modalTitle: { flex: 1, color: '#17352A', fontSize: 20, fontWeight: '900', paddingRight: 12 },
  modalClose: { width: 42, height: 42, borderRadius: 21, backgroundColor: '#F1F5F3', alignItems: 'center', justifyContent: 'center' },
  formGroup: { marginBottom: 16 },
  photoActionRow: { flexDirection: 'row', columnGap: 10, marginBottom: 10 },
  photoActionButton: { flex: 1, minHeight: 48, borderColor: '#B8D5C8', borderWidth: 1, borderRadius: 13, backgroundColor: '#F4FAF7', flexDirection: 'row', alignItems: 'center', justifyContent: 'center', columnGap: 7 },
  photoActionText: { color: '#0B5D3B', fontSize: 13, fontWeight: '900' },
  formLabel: { color: '#29483D', fontSize: 13, fontWeight: '800', marginBottom: 7 },
  formInput: { minHeight: 49, borderColor: '#CBD9D3', borderWidth: 1, borderRadius: 12, backgroundColor: '#FFFFFF', color: '#17352A', fontSize: 15, paddingHorizontal: 14 },
  trackingInputRow: { flexDirection: 'row', alignItems: 'center', columnGap: 10 },
  trackingInput: { flex: 1 },
  scanTrackingButton: { minHeight: 49, borderRadius: 12, backgroundColor: '#0B5D3B', paddingHorizontal: 14, flexDirection: 'row', alignItems: 'center', justifyContent: 'center', columnGap: 6 },
  scanTrackingText: { color: '#FFFFFF', fontSize: 13, fontWeight: '900' },
  scannerScreen: { flex: 1, backgroundColor: '#000000' },
  scannerCamera: { flex: 1 },
  scannerOverlay: { flex: 1, backgroundColor: 'rgba(0, 0, 0, 0.18)' },
  scannerTopBar: { minHeight: 92, paddingHorizontal: 20, paddingTop: 34, flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between' },
  scannerTitle: { color: '#FFFFFF', fontSize: 18, fontWeight: '900' },
  scannerCloseButton: { width: 44, height: 44, borderRadius: 22, backgroundColor: 'rgba(0, 0, 0, 0.35)', alignItems: 'center', justifyContent: 'center' },
  scannerGuide: { flex: 1, alignItems: 'center', justifyContent: 'center', paddingHorizontal: 28 },
  scannerFrame: { width: '100%', maxWidth: 330, height: 150, borderColor: '#FFFFFF', borderWidth: 3, borderRadius: 18, backgroundColor: 'rgba(255, 255, 255, 0.08)' },
  scannerHint: { color: '#FFFFFF', fontSize: 14, fontWeight: '800', textAlign: 'center', marginTop: 18, lineHeight: 20 },
  shippingTaxPreview: { backgroundColor: '#F4FAF7', borderColor: '#DCE9E3', borderWidth: 1, borderRadius: 12, marginTop: 10, padding: 12, rowGap: 8 },
  shippingTaxRow: { flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', columnGap: 12 },
  shippingTaxLabel: { color: '#587066', fontSize: 12, fontWeight: '800' },
  shippingTaxValue: { color: '#29483D', fontSize: 13, fontWeight: '900' },
  shippingTaxTotalLabel: { color: '#17352A', fontSize: 13, fontWeight: '900' },
  shippingTaxTotalValue: { color: '#0B5D3B', fontSize: 16, fontWeight: '900' },
  courierSelect: { minHeight: 49, borderColor: '#CBD9D3', borderWidth: 1, borderRadius: 12, backgroundColor: '#FFFFFF', paddingHorizontal: 14, flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between' },
  courierSelectText: { color: '#17352A', fontSize: 15, flex: 1 },
  courierPlaceholder: { color: '#82958D' },
  courierMenu: { borderColor: '#CBD9D3', borderWidth: 1, borderRadius: 12, backgroundColor: '#FFFFFF', marginTop: 7, overflow: 'hidden' },
  courierOption: { minHeight: 44, borderBottomColor: '#E7ECEA', borderBottomWidth: 1, paddingHorizontal: 13, flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between' },
  courierOptionText: { color: '#29483D', fontSize: 14, fontWeight: '700' },
  customCourierInput: { marginTop: 8 },
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
