import { useCallback, useEffect, useMemo, useState } from 'react';
import MaterialCommunityIcons from '@expo/vector-icons/MaterialCommunityIcons';
import {
  ActivityIndicator,
  Alert,
  FlatList,
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

import * as api from '../auth/api';
import { useAuth } from '../auth/AuthContext';
import type { CustomerAddressInput, CustomerSummary } from './types';
import type { Money, OrderDetail, OrderSummary } from '../orders/types';
import type { ProductSummary } from '../stock/types';

const EMPTY_CUSTOMER_FORM: CustomerAddressInput = {
  name: '',
  phone: '',
  email: '',
  address_1: '',
  address_2: '',
  city: '',
  state: '',
  pincode: '',
  country: 'India',
};

function money(value: Money | null | undefined) {
  if (!value) return '₹ 0.00';
  return `${value.currency === 'INR' ? '₹' : value.currency} ${value.amount}`;
}

function newIdempotencyKey() {
  return `android-manual-order-${Date.now()}-${Math.random().toString(36).slice(2, 12)}`;
}

function parseAmount(value: Money | null | undefined) {
  const amount = Number(value?.amount || '0');
  return Number.isFinite(amount) ? amount : 0;
}

function productUnitPrice(product: ProductSummary) {
  return parseAmount(product.prices?.sale || product.prices?.regular);
}

function rupees(value: number) {
  return `₹ ${value.toFixed(2)}`;
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

function ManualOrderSheet({
  customer,
  visible,
  onClose,
  onCreated,
}: {
  customer: CustomerSummary;
  visible: boolean;
  onClose: () => void;
  onCreated: (order: OrderDetail) => void;
}) {
  const { runAuthenticated } = useAuth();
  const [search, setSearch] = useState('');
  const [products, setProducts] = useState<ProductSummary[]>([]);
  const [selectedItems, setSelectedItems] = useState<{ product: ProductSummary; quantity: number }[]>([]);
  const [shippingMode, setShippingMode] = useState<'free' | 'charged'>('free');
  const [shippingCost, setShippingCost] = useState('');
  const [loadingProducts, setLoadingProducts] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');

  const loadProducts = useCallback(async () => {
    setLoadingProducts(true);
    setError('');
    try {
      const response = await runAuthenticated((token) => api.products(token, { search }));
      setProducts(response.data.slice(0, 25));
    } catch (reason) {
      setError(reason instanceof api.ApiError ? reason.message : 'Products could not be loaded.');
    } finally {
      setLoadingProducts(false);
    }
  }, [runAuthenticated, search]);

  useEffect(() => {
    if (visible) void loadProducts();
  }, [visible, loadProducts]);

  const addProduct = (product: ProductSummary) => {
    setSelectedItems((current) => {
      const existing = current.find((item) => item.product.id === product.id);
      if (existing) {
        return current.map((item) => (
          item.product.id === product.id ? { ...item, quantity: item.quantity + 1 } : item
        ));
      }
      return [...current, { product, quantity: 1 }];
    });
  };

  const updateQuantity = (productId: number, quantityText: string) => {
    const quantity = Math.max(1, Number.parseInt(quantityText.replace(/\D/g, ''), 10) || 1);
    setSelectedItems((current) => current.map((item) => (
      item.product.id === productId ? { ...item, quantity } : item
    )));
  };

  const removeProduct = (productId: number) => {
    setSelectedItems((current) => current.filter((item) => item.product.id !== productId));
  };

  const productsTotal = selectedItems.reduce((sum, item) => sum + productUnitPrice(item.product) * item.quantity, 0);
  const shippingAmount = shippingMode === 'charged' ? Number.parseFloat(shippingCost.replace(/[^0-9.]/g, '')) || 0 : 0;
  const total = productsTotal + shippingAmount;
  const canSave = selectedItems.length > 0 && (shippingMode === 'free' || shippingAmount > 0) && !saving;

  const sendWhatsApp = async (phone: string, confirmationUrl: string) => {
    const digits = normalizePhone(phone);
    if (!digits) return;
    const url = `https://wa.me/91${digits}?text=${encodeURIComponent(confirmationUrl)}`;
    await Linking.openURL(url).catch(() => {
      Alert.alert('WhatsApp unavailable', 'The confirmation link is ready, but WhatsApp could not be opened.');
    });
  };

  const createOrder = async () => {
    if (!canSave) return;
    setSaving(true);
    setError('');
    try {
      const response = await runAuthenticated((token) => api.createManualOrder(
        token,
        {
          customer_key: customer.key,
          items: selectedItems.map((item) => ({ product_id: item.product.id, quantity: item.quantity })),
          shipping_mode: shippingMode,
          shipping_base_amount: shippingAmount.toFixed(2),
        },
        newIdempotencyKey(),
      ));
      setSelectedItems([]);
      setShippingMode('free');
      setShippingCost('');
      onCreated(response.data.order);
      onClose();
      await sendWhatsApp(response.data.whatsapp.phone, response.data.whatsapp.confirmation_url);
    } catch (reason) {
      setError(reason instanceof api.ApiError ? reason.message : 'Manual order could not be created.');
    } finally {
      setSaving(false);
    }
  };

  return (
    <Modal animationType="slide" onRequestClose={() => !saving && onClose()} transparent visible={visible}>
      <KeyboardAvoidingView behavior={Platform.OS === 'ios' ? 'padding' : 'height'} style={styles.modalKeyboardView}>
        <View style={styles.modalBackdrop}>
          <View style={styles.formSheet}>
            <View style={styles.formHeader}>
              <View style={styles.sheetTitleCopy}>
                <Text style={styles.formTitle}>Create manual order</Text>
              </View>
              <Pressable disabled={saving} onPress={onClose} style={styles.closeButton}>
                <MaterialCommunityIcons color="#587066" name="close" size={24} />
              </Pressable>
            </View>
            <ScrollView keyboardShouldPersistTaps="handled" showsVerticalScrollIndicator={false}>
              <View style={styles.searchRow}>
                <TextInput
                  autoCapitalize="none"
                  onChangeText={setSearch}
                  onSubmitEditing={() => void loadProducts()}
                  placeholder="Search product or SKU"
                  placeholderTextColor="#82958D"
                  returnKeyType="search"
                  style={styles.searchInput}
                  value={search}
                />
                <Pressable onPress={() => void loadProducts()} style={styles.searchButton}>
                  <MaterialCommunityIcons color="#FFFFFF" name="magnify" size={23} />
                </Pressable>
              </View>
              {error ? <Text style={styles.formError}>{error}</Text> : null}
              {selectedItems.length ? (
                <View style={styles.selectedCard}>
                  <Text style={styles.selectedTitle}>Selected products</Text>
                  {selectedItems.map((item) => (
                    <View key={item.product.id} style={styles.selectedRow}>
                      <View style={styles.selectedCopy}>
                        <Text numberOfLines={1} style={styles.selectedName}>{item.product.name}</Text>
                        <Text style={styles.selectedMeta}>{rupees(productUnitPrice(item.product))} each</Text>
                      </View>
                      <TextInput
                        keyboardType="number-pad"
                        onChangeText={(value) => updateQuantity(item.product.id, value)}
                        style={styles.qtyInput}
                        value={String(item.quantity)}
                      />
                      <Pressable onPress={() => removeProduct(item.product.id)} style={styles.removeItemButton}>
                        <MaterialCommunityIcons color="#B42318" name="trash-can-outline" size={19} />
                      </Pressable>
                    </View>
                  ))}
                  <View style={styles.shippingChoiceBlock}>
                    <Text style={styles.selectedTitle}>Shipping</Text>
                    <View style={styles.shippingModeRow}>
                      <Pressable onPress={() => setShippingMode('free')} style={[styles.shippingModeButton, shippingMode === 'free' && styles.shippingModeButtonActive]}>
                        <MaterialCommunityIcons color={shippingMode === 'free' ? '#FFFFFF' : '#0B5D3B'} name="truck-check-outline" size={18} />
                        <Text style={[styles.shippingModeText, shippingMode === 'free' && styles.shippingModeTextActive]}>Free</Text>
                      </Pressable>
                      <Pressable onPress={() => setShippingMode('charged')} style={[styles.shippingModeButton, shippingMode === 'charged' && styles.shippingModeButtonActive]}>
                        <MaterialCommunityIcons color={shippingMode === 'charged' ? '#FFFFFF' : '#0B5D3B'} name="truck-fast-outline" size={18} />
                        <Text style={[styles.shippingModeText, shippingMode === 'charged' && styles.shippingModeTextActive]}>Charged</Text>
                      </Pressable>
                    </View>
                    {shippingMode === 'charged' ? (
                      <>
                        <TextInput
                          keyboardType="decimal-pad"
                          onChangeText={setShippingCost}
                          placeholder="Shipping amount"
                          placeholderTextColor="#82958D"
                          style={styles.shippingCostInput}
                          value={shippingCost}
                        />
                        <Text style={styles.selectedMeta}>Shipping added to total: {rupees(shippingAmount)}</Text>
                      </>
                    ) : (
                      <Text style={styles.selectedMeta}>Shipping will show as Free in customer summary.</Text>
                    )}
                  </View>
                  <View style={styles.manualTotalRow}>
                    <Text style={styles.manualTotalLabel}>Order total</Text>
                    <Text style={styles.manualTotalValue}>{rupees(total)}</Text>
                  </View>
                </View>
              ) : (
                <Text style={styles.emptyText}>Select products for this order.</Text>
              )}
              <Text style={styles.sectionTitle}>Products</Text>
              {loadingProducts ? <ActivityIndicator color="#0B5D3B" /> : products.map((product) => (
                <Pressable key={product.id} onPress={() => addProduct(product)} style={({ pressed }) => [styles.productPickRow, pressed && styles.pressed]}>
                  <View style={styles.productPickIcon}>
                    <MaterialCommunityIcons color="#0B5D3B" name="package-variant-closed" size={20} />
                  </View>
                  <View style={styles.productPickCopy}>
                    <Text numberOfLines={1} style={styles.productPickName}>{product.name}</Text>
                    <Text numberOfLines={1} style={styles.productPickMeta}>
                      {product.sku || 'No SKU'} · {product.stock_quantity} available · {rupees(productUnitPrice(product))}
                    </Text>
                  </View>
                  <MaterialCommunityIcons color="#0B5D3B" name="plus-circle-outline" size={24} />
                </Pressable>
              ))}
            </ScrollView>
            <Pressable disabled={!canSave} onPress={() => void createOrder()} style={[styles.saveButton, !canSave && styles.disabledButton]}>
              {saving ? <ActivityIndicator color="#FFFFFF" /> : <Text style={styles.saveButtonText}>Create order</Text>}
            </Pressable>
          </View>
        </View>
      </KeyboardAvoidingView>
    </Modal>
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
  const [manualOrderVisible, setManualOrderVisible] = useState(false);
  const [editVisible, setEditVisible] = useState(false);
  const [editValues, setEditValues] = useState<CustomerAddressInput>(EMPTY_CUSTOMER_FORM);
  const [editSaving, setEditSaving] = useState(false);
  const [editError, setEditError] = useState('');
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
  const editReady = Boolean(
    editValues.name.trim()
    && editValues.phone.replace(/\D/g, '').length >= 10
  );
  const openEdit = () => {
    if (!customer.shipping_address) return;
    setEditValues({
      name: customer.shipping_address.name || customer.name,
      phone: customer.shipping_address.phone || customer.phone || '',
      email: customer.shipping_address.email || customer.email || '',
      address_1: customer.shipping_address.address_1 || '',
      address_2: customer.shipping_address.address_2 || '',
      city: customer.shipping_address.city || '',
      state: customer.shipping_address.state || '',
      pincode: customer.shipping_address.pincode || '',
      country: customer.shipping_address.country || 'India',
    });
    setEditError('');
    setEditVisible(true);
  };
  const updateEdit = (field: keyof CustomerAddressInput, value: string) => {
    setEditValues((current) => ({ ...current, [field]: value }));
  };
  const saveEdit = async () => {
    if (!editReady || editSaving) return;
    setEditSaving(true);
    setEditError('');
    try {
      const payload: CustomerAddressInput = {
        name: editValues.name.trim(),
        phone: editValues.phone.trim(),
        email: editValues.email?.trim() || '',
        address_1: editValues.address_1.trim(),
        address_2: editValues.address_2?.trim() || '',
        city: editValues.city.trim(),
        state: editValues.state.trim(),
        pincode: editValues.pincode.trim(),
        country: editValues.country?.trim() || 'India',
      };
      const response = await runAuthenticated((token) => api.updateCustomer(token, customerKey, payload));
      setCustomer(response.data.customer);
      setOrders(response.data.orders);
      setEditVisible(false);
    } catch (reason) {
      setEditError(reason instanceof api.ApiError ? reason.message : 'Customer could not be updated.');
    } finally {
      setEditSaving(false);
    }
  };
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

      {customer.source === 'saved' ? (
        <Pressable onPress={openEdit} style={({ pressed }) => [styles.editCustomerButton, pressed && styles.pressed]}>
          <MaterialCommunityIcons color="#0B5D3B" name="pencil-outline" size={20} />
          <Text style={styles.editCustomerText}>Edit customer details</Text>
        </Pressable>
      ) : null}

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

      <Pressable onPress={() => setManualOrderVisible(true)} style={({ pressed }) => [styles.manualOrderButton, pressed && styles.pressed]}>
        <MaterialCommunityIcons color="#FFFFFF" name="cart-plus" size={22} />
        <Text style={styles.manualOrderText}>Create manual order</Text>
      </Pressable>

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

      <Modal animationType="slide" onRequestClose={() => !editSaving && setEditVisible(false)} transparent visible={editVisible}>
        <KeyboardAvoidingView behavior={Platform.OS === 'ios' ? 'padding' : 'height'} style={styles.modalKeyboardView}>
          <View style={styles.modalBackdrop}>
            <View style={styles.formSheet}>
              <View style={styles.formHeader}>
                <View>
                  <Text style={styles.formTitle}>Edit customer</Text>
                  <Text style={styles.formSubtitle}>Updated details will be used for future labels.</Text>
                </View>
                <Pressable disabled={editSaving} onPress={() => setEditVisible(false)} style={styles.closeButton}>
                  <MaterialCommunityIcons color="#587066" name="close" size={24} />
                </Pressable>
              </View>
              <ScrollView keyboardShouldPersistTaps="handled" showsVerticalScrollIndicator={false}>
                <Text style={styles.inputLabel}>Customer name *</Text>
                <TextInput autoCapitalize="words" maxLength={160} onChangeText={(value) => updateEdit('name', value)} placeholder="Customer name" placeholderTextColor="#82958D" style={styles.input} value={editValues.name} />
                <Text style={styles.inputLabel}>Mobile number *</Text>
                <TextInput keyboardType="phone-pad" maxLength={32} onChangeText={(value) => updateEdit('phone', value)} placeholder="10-digit mobile number" placeholderTextColor="#82958D" style={styles.input} value={editValues.phone} />
                <Text style={styles.inputLabel}>Email</Text>
                <TextInput autoCapitalize="none" keyboardType="email-address" maxLength={254} onChangeText={(value) => updateEdit('email', value)} placeholder="Optional" placeholderTextColor="#82958D" style={styles.input} value={editValues.email} />
                <Text style={styles.inputLabel}>Address line 1</Text>
                <TextInput maxLength={255} onChangeText={(value) => updateEdit('address_1', value)} placeholder="House number and street" placeholderTextColor="#82958D" style={styles.input} value={editValues.address_1} />
                <Text style={styles.inputLabel}>Address line 2</Text>
                <TextInput maxLength={255} onChangeText={(value) => updateEdit('address_2', value)} placeholder="Area or landmark" placeholderTextColor="#82958D" style={styles.input} value={editValues.address_2} />
                <View style={styles.formRow}>
                  <View style={styles.formHalf}>
                    <Text style={styles.inputLabel}>City</Text>
                    <TextInput maxLength={120} onChangeText={(value) => updateEdit('city', value)} placeholder="City" placeholderTextColor="#82958D" style={styles.input} value={editValues.city} />
                  </View>
                  <View style={styles.formHalf}>
                    <Text style={styles.inputLabel}>State</Text>
                    <TextInput maxLength={120} onChangeText={(value) => updateEdit('state', value)} placeholder="State" placeholderTextColor="#82958D" style={styles.input} value={editValues.state} />
                  </View>
                </View>
                <View style={styles.formRow}>
                  <View style={styles.formHalf}>
                    <Text style={styles.inputLabel}>Pincode</Text>
                    <TextInput keyboardType="number-pad" maxLength={20} onChangeText={(value) => updateEdit('pincode', value)} placeholder="Pincode" placeholderTextColor="#82958D" style={styles.input} value={editValues.pincode} />
                  </View>
                  <View style={styles.formHalf}>
                    <Text style={styles.inputLabel}>Country</Text>
                    <TextInput maxLength={120} onChangeText={(value) => updateEdit('country', value)} placeholder="India" placeholderTextColor="#82958D" style={styles.input} value={editValues.country} />
                  </View>
                </View>
                {editError ? <Text style={styles.formError}>{editError}</Text> : null}
              </ScrollView>
              <Pressable disabled={!editReady || editSaving} onPress={() => void saveEdit()} style={[styles.saveButton, (!editReady || editSaving) && styles.disabledButton]}>
                {editSaving ? <ActivityIndicator color="#FFFFFF" /> : <Text style={styles.saveButtonText}>Save changes</Text>}
              </Pressable>
            </View>
          </View>
        </KeyboardAvoidingView>
      </Modal>
      <ManualOrderSheet
        customer={customer}
        visible={manualOrderVisible}
        onClose={() => setManualOrderVisible(false)}
        onCreated={(order) => {
          setSelectedOrder(order);
          void load(true);
        }}
      />
    </ScrollView>
  );
}

export default function CustomersScreen() {
  const { runAuthenticated } = useAuth();
  const [customers, setCustomers] = useState<CustomerSummary[]>([]);
  const [selectedCustomerKey, setSelectedCustomerKey] = useState<string | null>(null);
  const [draftSearch, setDraftSearch] = useState('');
  const [search, setSearch] = useState('');
  const [formVisible, setFormVisible] = useState(false);
  const [formValues, setFormValues] = useState<CustomerAddressInput>(EMPTY_CUSTOMER_FORM);
  const [formAddressMode, setFormAddressMode] = useState<'paste' | 'later'>('later');
  const [formSaving, setFormSaving] = useState(false);
  const [formError, setFormError] = useState('');
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
  const formReady = Boolean(
    formValues.name.trim()
    && formValues.phone.replace(/\D/g, '').length >= 10
  );

  const openAddCustomer = () => {
    setFormValues(EMPTY_CUSTOMER_FORM);
    setFormAddressMode('later');
    setFormError('');
    setFormVisible(true);
  };

  const updateForm = (field: keyof CustomerAddressInput, value: string) => {
    setFormValues((current) => ({ ...current, [field]: value }));
  };

  const saveCustomer = async () => {
    if (!formReady || formSaving) return;
    setFormSaving(true);
    setFormError('');
    try {
      const payload: CustomerAddressInput = {
        name: formValues.name.trim(),
        phone: formValues.phone.trim(),
        email: formValues.email?.trim() || '',
        address_1: formAddressMode === 'paste' ? formValues.address_1.trim() : '',
        address_2: '',
        city: '',
        state: '',
        pincode: '',
        country: formValues.country?.trim() || 'India',
      };
      const response = await runAuthenticated((token) => api.createCustomer(token, payload));
      setFormVisible(false);
      setSelectedCustomerKey(response.data.customer.key);
      await load(true);
    } catch (reason) {
      setFormError(reason instanceof api.ApiError ? reason.message : 'Customer could not be saved.');
    } finally {
      setFormSaving(false);
    }
  };

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
        <Pressable onPress={openAddCustomer} style={({ pressed }) => [styles.addCustomerButton, pressed && styles.pressed]}>
          <MaterialCommunityIcons color="#0B5D3B" name="account-plus-outline" size={21} />
          <Text style={styles.addCustomerText}>Add Customer</Text>
        </Pressable>
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
    <>
      <FlatList
        contentContainerStyle={styles.listContent}
        data={customers}
        keyExtractor={(item) => item.key}
        ListEmptyComponent={<View style={styles.emptyState}><Text style={styles.emptyTitle}>No customers found</Text><Text style={styles.emptyText}>Try another search or add a customer.</Text></View>}
        ListHeaderComponent={header}
        refreshControl={<RefreshControl refreshing={refreshing} onRefresh={() => void load(true)} colors={['#0B5D3B']} tintColor="#0B5D3B" />}
        renderItem={({ item }) => <CustomerCard customer={item} onPress={() => setSelectedCustomerKey(item.key)} />}
      />
      <Modal animationType="slide" onRequestClose={() => !formSaving && setFormVisible(false)} transparent visible={formVisible}>
        <KeyboardAvoidingView behavior={Platform.OS === 'ios' ? 'padding' : 'height'} style={styles.modalKeyboardView}>
          <View style={styles.modalBackdrop}>
            <View style={styles.formSheet}>
              <View style={styles.formHeader}>
                <View>
                  <Text style={styles.formTitle}>Add Customer</Text>
                </View>
                <Pressable disabled={formSaving} onPress={() => setFormVisible(false)} style={styles.closeButton}>
                  <MaterialCommunityIcons color="#587066" name="close" size={24} />
                </Pressable>
              </View>
              <ScrollView keyboardShouldPersistTaps="handled" showsVerticalScrollIndicator={false}>
                <Text style={styles.inputLabel}>Customer name *</Text>
                <TextInput autoCapitalize="words" maxLength={160} onChangeText={(value) => updateForm('name', value)} placeholder="Customer name" placeholderTextColor="#82958D" style={styles.input} value={formValues.name} />
                <Text style={styles.inputLabel}>Mobile number *</Text>
                <TextInput keyboardType="phone-pad" maxLength={32} onChangeText={(value) => updateForm('phone', value)} placeholder="10-digit mobile number" placeholderTextColor="#82958D" style={styles.input} value={formValues.phone} />
                <Text style={styles.inputLabel}>Email</Text>
                <TextInput autoCapitalize="none" keyboardType="email-address" maxLength={254} onChangeText={(value) => updateForm('email', value)} placeholder="Optional" placeholderTextColor="#82958D" style={styles.input} value={formValues.email} />
                <Text style={styles.inputLabel}>Address option</Text>
                <View style={styles.addressModeRow}>
                  <Pressable onPress={() => setFormAddressMode('later')} style={[styles.addressModeButton, formAddressMode === 'later' && styles.addressModeButtonActive]}>
                    <MaterialCommunityIcons color={formAddressMode === 'later' ? '#FFFFFF' : '#0B5D3B'} name="link-variant" size={18} />
                    <Text style={[styles.addressModeText, formAddressMode === 'later' && styles.addressModeTextActive]}>Customer enters in link</Text>
                  </Pressable>
                  <Pressable onPress={() => setFormAddressMode('paste')} style={[styles.addressModeButton, formAddressMode === 'paste' && styles.addressModeButtonActive]}>
                    <MaterialCommunityIcons color={formAddressMode === 'paste' ? '#FFFFFF' : '#0B5D3B'} name="content-paste" size={18} />
                    <Text style={[styles.addressModeText, formAddressMode === 'paste' && styles.addressModeTextActive]}>Paste address</Text>
                  </Pressable>
                </View>
                {formAddressMode === 'later' ? (
                  <View style={styles.addressLaterNote}>
                    <Text style={styles.addressLaterText}>Only name and mobile will be saved. The customer must enter address before confirming the order.</Text>
                  </View>
                ) : (
                  <>
                    <Text style={styles.inputLabel}>Paste full address</Text>
                    <TextInput
                      maxLength={600}
                      multiline
                      onChangeText={(value) => updateForm('address_1', value)}
                      placeholder="Paste the customer address exactly as received"
                      placeholderTextColor="#82958D"
                      style={[styles.input, styles.pasteAddressInput]}
                      textAlignVertical="top"
                      value={formValues.address_1}
                    />
                  </>
                )}
                {formError ? <Text style={styles.formError}>{formError}</Text> : null}
              </ScrollView>
              <Pressable disabled={!formReady || formSaving} onPress={() => void saveCustomer()} style={[styles.saveButton, (!formReady || formSaving) && styles.disabledButton]}>
                {formSaving ? <ActivityIndicator color="#FFFFFF" /> : <Text style={styles.saveButtonText}>Save customer</Text>}
              </Pressable>
            </View>
          </View>
        </KeyboardAvoidingView>
      </Modal>
    </>
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
  addCustomerButton: { minHeight: 48, backgroundColor: '#FFFFFF', borderRadius: 14, flexDirection: 'row', alignItems: 'center', justifyContent: 'center', columnGap: 8, marginTop: 16 },
  addCustomerText: { color: '#0B5D3B', fontSize: 15, fontWeight: '900' },
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
  editCustomerButton: { minHeight: 46, backgroundColor: '#F4FAF7', borderColor: '#B8D5C8', borderWidth: 1, borderRadius: 14, flexDirection: 'row', alignItems: 'center', justifyContent: 'center', columnGap: 8, marginBottom: 12 },
  editCustomerText: { color: '#0B5D3B', fontSize: 14, fontWeight: '900' },
  contactGrid: { flexDirection: 'row', columnGap: 10, marginBottom: 20 },
  contactButton: { flex: 1, minHeight: 50, borderColor: '#B8D5C8', borderWidth: 1, borderRadius: 14, backgroundColor: '#F4FAF7', alignItems: 'center', justifyContent: 'center', flexDirection: 'row', columnGap: 8 },
  whatsAppButton: { backgroundColor: '#ECF9F1' },
  contactButtonText: { color: '#0B5D3B', fontSize: 14, fontWeight: '900' },
  disabledCard: { opacity: 0.45 },
  manualOrderButton: { minHeight: 62, backgroundColor: '#104D34', borderRadius: 17, flexDirection: 'row', alignItems: 'center', paddingHorizontal: 16, columnGap: 12, marginTop: -10, marginBottom: 22 },
  manualOrderText: { color: '#FFFFFF', fontSize: 16, fontWeight: '900' },
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
  modalKeyboardView: { flex: 1 },
  modalBackdrop: { flex: 1, justifyContent: 'flex-end', backgroundColor: 'rgba(15, 35, 28, 0.46)' },
  formSheet: { maxHeight: '90%', backgroundColor: '#FFFFFF', borderTopLeftRadius: 24, borderTopRightRadius: 24, padding: 18, paddingBottom: 28 },
  formHeader: { flexDirection: 'row', alignItems: 'flex-start', justifyContent: 'space-between', columnGap: 14, marginBottom: 14 },
  sheetTitleCopy: { flex: 1 },
  formTitle: { color: '#17352A', fontSize: 22, fontWeight: '900' },
  formSubtitle: { color: '#71867D', fontSize: 12, lineHeight: 18, marginTop: 3 },
  closeButton: { width: 40, height: 40, borderRadius: 20, backgroundColor: '#F3F8F5', alignItems: 'center', justifyContent: 'center' },
  inputLabel: { color: '#40564D', fontSize: 12, fontWeight: '900', marginBottom: 6 },
  input: { minHeight: 50, borderColor: '#CAD7D1', borderWidth: 1, borderRadius: 13, color: '#17352A', fontSize: 15, paddingHorizontal: 13, marginBottom: 13, backgroundColor: '#FFFFFF' },
  pasteAddressInput: { minHeight: 110, paddingTop: 12, lineHeight: 20 },
  formRow: { flexDirection: 'row', columnGap: 10 },
  formHalf: { flex: 1 },
  addressModeRow: { flexDirection: 'row', columnGap: 8, marginBottom: 12 },
  addressModeButton: { flex: 1, minHeight: 48, borderColor: '#B8D5C8', borderWidth: 1, borderRadius: 14, backgroundColor: '#FFFFFF', flexDirection: 'row', alignItems: 'center', justifyContent: 'center', columnGap: 6, paddingHorizontal: 8 },
  addressModeButtonActive: { backgroundColor: '#0B5D3B', borderColor: '#0B5D3B' },
  addressModeText: { color: '#0B5D3B', fontSize: 11, fontWeight: '900', textAlign: 'center' },
  addressModeTextActive: { color: '#FFFFFF' },
  addressLaterNote: { backgroundColor: '#F4FAF7', borderColor: '#B8D5C8', borderWidth: 1, borderRadius: 14, padding: 12, marginBottom: 13 },
  addressLaterText: { color: '#40564D', fontSize: 12, fontWeight: '700', lineHeight: 18 },
  formError: { color: '#B42318', fontSize: 13, lineHeight: 18, marginBottom: 12 },
  saveButton: { minHeight: 52, borderRadius: 15, backgroundColor: '#0B5D3B', alignItems: 'center', justifyContent: 'center', marginTop: 4 },
  saveButtonText: { color: '#FFFFFF', fontSize: 16, fontWeight: '900' },
  disabledButton: { opacity: 0.45 },
  selectedCard: { backgroundColor: '#F4FAF7', borderColor: '#B8D5C8', borderWidth: 1, borderRadius: 16, padding: 13, marginBottom: 18 },
  selectedTitle: { color: '#17352A', fontSize: 15, fontWeight: '900', marginBottom: 9 },
  selectedRow: { flexDirection: 'row', alignItems: 'center', columnGap: 9, paddingVertical: 7 },
  selectedCopy: { flex: 1 },
  selectedName: { color: '#17352A', fontSize: 14, fontWeight: '900' },
  selectedMeta: { color: '#71867D', fontSize: 11, fontWeight: '700', marginTop: 3 },
  qtyInput: { width: 52, minHeight: 42, borderColor: '#CAD7D1', borderWidth: 1, borderRadius: 12, color: '#17352A', fontSize: 16, fontWeight: '900', textAlign: 'center', backgroundColor: '#FFFFFF' },
  removeItemButton: { width: 38, height: 38, borderRadius: 19, alignItems: 'center', justifyContent: 'center', backgroundColor: '#FFF2F2' },
  shippingChoiceBlock: { borderTopColor: '#DCE5E1', borderTopWidth: 1, marginTop: 10, paddingTop: 12 },
  shippingModeRow: { flexDirection: 'row', columnGap: 9, marginBottom: 10 },
  shippingModeButton: { flex: 1, minHeight: 44, borderColor: '#B8D5C8', borderWidth: 1, borderRadius: 13, backgroundColor: '#FFFFFF', flexDirection: 'row', alignItems: 'center', justifyContent: 'center', columnGap: 7 },
  shippingModeButtonActive: { backgroundColor: '#0B5D3B', borderColor: '#0B5D3B' },
  shippingModeText: { color: '#0B5D3B', fontSize: 13, fontWeight: '900' },
  shippingModeTextActive: { color: '#FFFFFF' },
  shippingCostInput: { minHeight: 48, borderColor: '#CAD7D1', borderWidth: 1, borderRadius: 13, color: '#17352A', fontSize: 15, fontWeight: '800', paddingHorizontal: 13, marginBottom: 6, backgroundColor: '#FFFFFF' },
  manualTotalRow: { borderTopColor: '#DCE5E1', borderTopWidth: 1, marginTop: 9, paddingTop: 12, flexDirection: 'row', justifyContent: 'space-between' },
  manualTotalLabel: { color: '#40564D', fontSize: 13, fontWeight: '900' },
  manualTotalValue: { color: '#0B5D3B', fontSize: 18, fontWeight: '900' },
  productPickRow: { backgroundColor: '#FFFFFF', borderColor: '#E0E7E3', borderWidth: 1, borderRadius: 15, padding: 12, marginBottom: 9, flexDirection: 'row', alignItems: 'center', columnGap: 10 },
  productPickIcon: { width: 38, height: 38, borderRadius: 13, backgroundColor: '#E4F3EB', alignItems: 'center', justifyContent: 'center' },
  productPickCopy: { flex: 1 },
  productPickName: { color: '#17352A', fontSize: 14, fontWeight: '900' },
  productPickMeta: { color: '#71867D', fontSize: 11, marginTop: 3, fontWeight: '700' },
});
