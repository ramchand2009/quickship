import { useCallback, useEffect, useState } from 'react';
import MaterialCommunityIcons from '@expo/vector-icons/MaterialCommunityIcons';
import {
  ActivityIndicator,
  Alert,
  FlatList,
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

import * as api from '../auth/api';
import { useAuth } from '../auth/AuthContext';
import type { Money } from '../orders/types';
import type { ProductDetail, ProductSummary, StockMovement } from './types';

const FILTERS = [
  { code: '', label: 'All' },
  { code: 'in_stock', label: 'In stock' },
  { code: 'low_stock', label: 'Low stock' },
  { code: 'out_of_stock', label: 'Out of stock' },
];

const STOCK_LABELS = { in_stock: 'In stock', low_stock: 'Low stock', out_of_stock: 'Out of stock' };
const money = (value: Money | null) => value ? `${value.currency === 'INR' ? '₹' : value.currency} ${value.amount}` : '';
const when = (value: string) => {
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? '' : date.toLocaleString([], { day: '2-digit', month: 'short', hour: '2-digit', minute: '2-digit' });
};
const newIdempotencyKey = () => `android-${Date.now()}-${Math.random().toString(36).slice(2, 12)}`;

function ProductCard({ product, onPress }: { product: ProductSummary; onPress: () => void }) {
  const critical = product.stock_state !== 'in_stock';
  return (
    <Pressable onPress={onPress} style={({ pressed }) => [styles.productCard, pressed && styles.pressed]}>
      {product.image_url ? <Image source={{ uri: product.image_url }} style={styles.productImage} /> : (
        <View style={styles.imageFallback}><Text style={styles.imageFallbackText}>{product.name.slice(0, 1).toUpperCase()}</Text></View>
      )}
      <View style={styles.productCopy}>
        <Text numberOfLines={2} style={styles.productName}>{product.name}</Text>
        <Text numberOfLines={1} style={styles.productMeta}>{product.sku}{product.category ? ` · ${product.category}` : ''}</Text>
        <View style={styles.stockRow}>
          <View style={[styles.stockBadge, critical && styles.stockBadgeCritical]}>
            <View style={[styles.stockDot, critical && styles.stockDotCritical]} />
            <Text style={[styles.stockQuantity, critical && styles.stockCritical]}>{STOCK_LABELS[product.stock_state]}</Text>
          </View>
          <Text style={styles.reorderText}>{product.stock_quantity} available · Reorder {product.reorder_level}</Text>
        </View>
      </View>
      <MaterialCommunityIcons color="#63766E" name="chevron-right" size={24} />
    </Pressable>
  );
}

function DetailRow({ label, value }: { label: string; value?: string | null }) {
  if (!value) return null;
  return <View style={styles.detailRow}><Text style={styles.detailLabel}>{label}</Text><Text selectable style={styles.detailValue}>{value}</Text></View>;
}

function ProductDetailScreen({ productId, onBack }: { productId: number; onBack: () => void }) {
  const { runAuthenticated } = useAuth();
  const [product, setProduct] = useState<ProductDetail | null>(null);
  const [movements, setMovements] = useState<StockMovement[]>([]);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState('');
  const [quantityEditorVisible, setQuantityEditorVisible] = useState(false);
  const [targetQuantity, setTargetQuantity] = useState('');
  const [adjustmentNote, setAdjustmentNote] = useState('');
  const [quantitySaving, setQuantitySaving] = useState(false);
  const [quantityError, setQuantityError] = useState('');

  const load = useCallback(async (refresh = false) => {
    if (refresh) setRefreshing(true); else setLoading(true);
    setError('');
    try {
      const [detail, history] = await runAuthenticated((token) => Promise.all([
        api.productDetail(token, productId), api.stockMovements(token, productId),
      ]));
      setProduct(detail.data);
      setMovements(history.data);
    } catch (reason) {
      setError(reason instanceof api.ApiError ? reason.message : 'Product details could not be loaded.');
    } finally {
      setLoading(false); setRefreshing(false);
    }
  }, [productId, runAuthenticated]);

  useEffect(() => { void load(); }, [load]);
  if (loading && !product) return <View style={styles.center}><ActivityIndicator size="large" color="#0B5D3B" /></View>;
  if (!product) return <View style={styles.center}><Text style={styles.errorTitle}>Product unavailable</Text><Text style={styles.errorMessage}>{error}</Text><Pressable onPress={onBack} style={styles.primaryButton}><Text style={styles.primaryText}>Back to stock</Text></Pressable></View>;

  const openQuantityEditor = () => {
    setTargetQuantity(String(product.stock_quantity));
    setAdjustmentNote('');
    setQuantityError('');
    setQuantityEditorVisible(true);
  };

  const quantityReady = /^\d+$/.test(targetQuantity.trim()) && Number(targetQuantity) <= 999999999;
  const submitQuantity = async () => {
    if (!quantityReady || quantitySaving) return;
    setQuantitySaving(true);
    setQuantityError('');
    try {
      const response = await runAuthenticated((token) => api.updateStockQuantity(
        token,
        product.id,
        {
          expected_quantity: product.stock_quantity,
          target_quantity: Number(targetQuantity),
          note: adjustmentNote.trim(),
        },
        newIdempotencyKey(),
      ));
      setProduct(response.data.product);
      if (response.data.movement) {
        setMovements((current) => [response.data.movement as StockMovement, ...current]);
      }
      setQuantityEditorVisible(false);
      const wooEffect = response.data.effects?.find((effect) => effect.code === 'woocommerce_sync');
      const wooMessage = wooEffect?.message ? `\n\n${wooEffect.message}` : '';
      Alert.alert(
        'Stock updated',
        (response.data.movement
          ? `Quantity changed from ${product.stock_quantity} to ${response.data.product.stock_quantity}.`
          : `Stock is already ${response.data.product.stock_quantity}.`) + wooMessage,
      );
    } catch (reason) {
      if (reason instanceof api.ApiError && reason.status === 409) {
        setQuantityEditorVisible(false);
        await load(true);
        Alert.alert('Stock was refreshed', 'The quantity changed before your update was saved. Review it and try again.');
      } else {
        setQuantityError(reason instanceof api.ApiError ? reason.message : 'Stock quantity could not be updated.');
      }
    } finally {
      setQuantitySaving(false);
    }
  };

  return (
    <>
    <ScrollView contentContainerStyle={styles.detailContent} refreshControl={<RefreshControl refreshing={refreshing} onRefresh={() => void load(true)} colors={['#0B5D3B']} tintColor="#0B5D3B" />}>
      <Pressable onPress={onBack} style={styles.backButton}>
        <MaterialCommunityIcons color="#0B5D3B" name="arrow-left" size={21} />
        <Text style={styles.backText}>Back to stock</Text>
      </Pressable>
      {error ? <View style={styles.warning}><Text style={styles.warningText}>{error} Showing the last loaded details.</Text></View> : null}
      <View style={styles.heroCard}>
        <Text style={styles.heroName}>{product.name}</Text>
        <Text style={styles.heroSku}>{product.sku}</Text>
        <View style={styles.quantityPanel}>
          <Text style={styles.quantityValue}>{product.stock_quantity}</Text>
          <View><Text style={styles.quantityLabel}>{STOCK_LABELS[product.stock_state]}</Text><Text style={styles.reorderText}>Reorder level {product.reorder_level}</Text></View>
        </View>
        {product.can_adjust_stock ? (
          <Pressable onPress={openQuantityEditor} style={({ pressed }) => [styles.adjustStockButton, pressed && styles.pressed]}>
            <MaterialCommunityIcons color="#FFFFFF" name="pencil-outline" size={20} />
            <Text style={styles.adjustStockText}>Update stock quantity</Text>
          </Pressable>
        ) : null}
      </View>

      <Text style={styles.sectionTitle}>Product information</Text>
      <View style={styles.sectionCard}>
        <DetailRow label="Category" value={product.category} />
        <DetailRow label="Barcode" value={product.barcode} />
        <DetailRow label="Description" value={product.description} />
        <DetailRow label="Routing" value={product.routing.ready ? 'Ready' : 'Needs attention'} />
        <DetailRow label="WooCommerce product" value={product.routing.woocommerce_product_id} />
        <DetailRow label="WooCommerce variation" value={product.routing.woocommerce_variation_id} />
      </View>

      {product.prices.actual || product.prices.regular || product.prices.sale ? <><Text style={styles.sectionTitle}>Prices</Text><View style={styles.sectionCard}><DetailRow label="Purchase price" value={money(product.prices.actual)} /><DetailRow label="Regular price" value={money(product.prices.regular)} /><DetailRow label="Sale price" value={money(product.prices.sale)} /></View></> : null}

      <Text style={styles.sectionTitle}>Recent stock movements</Text>
      <View style={styles.sectionCard}>
        {movements.length ? movements.map((movement, index) => (
          <View key={movement.id} style={[styles.movementRow, index > 0 && styles.divider]}>
            <View style={[styles.deltaBadge, movement.quantity_delta < 0 && styles.deltaNegative]}><Text style={[styles.deltaText, movement.quantity_delta < 0 && styles.deltaTextNegative]}>{movement.quantity_delta > 0 ? '+' : ''}{movement.quantity_delta}</Text></View>
            <View style={styles.movementCopy}>
              <Text style={styles.movementTitle}>{movement.movement_type.label} · balance {movement.quantity_after}</Text>
              {movement.note ? <Text style={styles.movementNote}>{movement.note}</Text> : null}
              <Text style={styles.movementTime}>{when(movement.created_at)}{movement.actor_display_name ? ` · ${movement.actor_display_name}` : ''}</Text>
            </View>
          </View>
        )) : <Text style={styles.emptyText}>No stock movement history is available.</Text>}
      </View>
    </ScrollView>
    <Modal
      animationType="slide"
      onRequestClose={() => !quantitySaving && setQuantityEditorVisible(false)}
      transparent
      visible={quantityEditorVisible}
    >
      <KeyboardAvoidingView behavior={Platform.OS === 'ios' ? 'padding' : 'height'} style={styles.modalKeyboardView}>
        <View style={styles.modalBackdrop}>
          <View style={styles.quantityModal}>
            <View style={styles.modalHeader}>
              <View style={styles.modalTitleWrap}>
                <Text style={styles.modalTitle}>Update stock quantity</Text>
                <Text style={styles.modalSubtitle}>{product.name} · Currently {product.stock_quantity}</Text>
              </View>
              <Pressable disabled={quantitySaving} onPress={() => setQuantityEditorVisible(false)} style={styles.modalClose}>
                <MaterialCommunityIcons color="#587066" name="close" size={24} />
              </Pressable>
            </View>
            <ScrollView
              contentContainerStyle={styles.quantityModalScroll}
              keyboardDismissMode="on-drag"
              keyboardShouldPersistTaps="handled"
              showsVerticalScrollIndicator={false}
            >
              <Text style={styles.formLabel}>New quantity</Text>
              <TextInput
                keyboardType="number-pad"
                maxLength={9}
                onChangeText={setTargetQuantity}
                placeholder="Enter available quantity"
                placeholderTextColor="#82958D"
                selectTextOnFocus
                style={styles.formInput}
                value={targetQuantity}
              />
              <Text style={styles.formHint}>This replaces the current available stock quantity.</Text>
              <Text style={[styles.formLabel, styles.noteLabel]}>Reason or note (optional)</Text>
              <TextInput
                maxLength={255}
                multiline
                onChangeText={setAdjustmentNote}
                placeholder="Example: Physical stock count"
                placeholderTextColor="#82958D"
                style={[styles.formInput, styles.noteInput]}
                value={adjustmentNote}
              />
              {quantityError ? <Text accessibilityRole="alert" style={styles.quantityError}>{quantityError}</Text> : null}
              <Pressable
                disabled={!quantityReady || quantitySaving}
                onPress={() => void submitQuantity()}
                style={[styles.saveQuantityButton, (!quantityReady || quantitySaving) && styles.disabledButton]}
              >
                {quantitySaving ? <ActivityIndicator color="#FFFFFF" /> : <Text style={styles.saveQuantityText}>Save quantity</Text>}
              </Pressable>
            </ScrollView>
          </View>
        </View>
      </KeyboardAvoidingView>
    </Modal>
    </>
  );
}

export default function StockScreen() {
  const { runAuthenticated } = useAuth();
  const [products, setProducts] = useState<ProductSummary[]>([]);
  const [draftSearch, setDraftSearch] = useState('');
  const [search, setSearch] = useState('');
  const [stockState, setStockState] = useState('');
  const [category, setCategory] = useState('');
  const [categories, setCategories] = useState<string[]>([]);
  const [categoryPickerVisible, setCategoryPickerVisible] = useState(false);
  const [categorySearch, setCategorySearch] = useState('');
  const [totalCount, setTotalCount] = useState(0);
  const [attentionCount, setAttentionCount] = useState(0);
  const [nextCursor, setNextCursor] = useState<string | null>(null);
  const [selectedId, setSelectedId] = useState<number | null>(null);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [loadingMore, setLoadingMore] = useState(false);
  const [error, setError] = useState('');

  const loadFirst = useCallback(async (refresh = false) => {
    if (refresh) setRefreshing(true); else setLoading(true);
    setError('');
    try {
      const response = await runAuthenticated((token) => api.products(token, { search, stock_state: stockState, category }));
      setProducts(response.data); setNextCursor(response.pagination.next_cursor);
      setTotalCount(response.meta?.total_count ?? response.data.length);
      setAttentionCount(response.meta?.attention_count ?? response.data.filter((product) => product.stock_state !== 'in_stock').length);
      setCategories(response.meta?.categories ?? []);
    } catch (reason) { setError(reason instanceof api.ApiError ? reason.message : 'Stock could not be loaded.'); }
    finally { setLoading(false); setRefreshing(false); }
  }, [category, runAuthenticated, search, stockState]);
  useEffect(() => { void loadFirst(); }, [loadFirst]);

  const loadMore = async () => {
    if (!nextCursor || loadingMore) return;
    setLoadingMore(true);
    try {
      const response = await runAuthenticated((token) => api.products(token, { search, stock_state: stockState, category, cursor: nextCursor }));
      setProducts((current) => [...current, ...response.data]); setNextCursor(response.pagination.next_cursor);
    } catch (reason) { setError(reason instanceof api.ApiError ? reason.message : 'More products could not be loaded.'); }
    finally { setLoadingMore(false); }
  };

  if (selectedId !== null) return <ProductDetailScreen productId={selectedId} onBack={() => setSelectedId(null)} />;
  if (loading && !products.length) return <View style={styles.center}><ActivityIndicator size="large" color="#0B5D3B" /><Text style={styles.loadingText}>Loading stock...</Text></View>;
  if (error && !products.length) return <View style={styles.center}><Text style={styles.errorTitle}>Stock unavailable</Text><Text style={styles.errorMessage}>{error}</Text><Pressable onPress={() => void loadFirst()} style={styles.primaryButton}><Text style={styles.primaryText}>Try again</Text></Pressable></View>;

  const header = (
    <View>
      <View style={styles.inventorySummary}>
        <View style={styles.summaryIcon}><MaterialCommunityIcons color="#14733D" name="package-variant-closed" size={26} /></View>
        <View style={styles.summaryCopy}>
          <Text style={styles.summaryValue}>{totalCount}</Text>
          <Text style={styles.summaryLabel}>Total products</Text>
        </View>
        <View style={styles.summaryDivider} />
        <View style={styles.summaryCopy}>
          <Text style={[styles.summaryValue, attentionCount > 0 && styles.summaryAttention]}>{attentionCount}</Text>
          <Text style={styles.summaryLabel}>Need attention</Text>
        </View>
      </View>
      <View style={styles.searchRow}>
        <TextInput
          accessibilityLabel="Search stock"
          autoCapitalize="none"
          onChangeText={setDraftSearch}
          onSubmitEditing={() => setSearch(draftSearch.trim())}
          placeholder="Product, SKU, or barcode"
          placeholderTextColor="#82958D"
          returnKeyType="search"
          style={styles.searchInput}
          value={draftSearch}
        />
        <Pressable accessibilityLabel="Search stock" onPress={() => setSearch(draftSearch.trim())} style={styles.searchButton}>
          <MaterialCommunityIcons color="#FFFFFF" name="magnify" size={23} />
        </Pressable>
      </View>
      <ScrollView horizontal showsHorizontalScrollIndicator={false} contentContainerStyle={styles.filterRow}>
        {FILTERS.map((filter) => (
          <Pressable key={filter.code} onPress={() => setStockState(filter.code)} style={[styles.filterChip, stockState === filter.code && styles.filterActive]}>
            <Text style={[styles.filterText, stockState === filter.code && styles.filterTextActive]}>{filter.label}</Text>
          </Pressable>
        ))}
      </ScrollView>
      <View style={styles.categoryFilterBlock}>
        <Text style={styles.categoryFilterLabel}>Category</Text>
        <Pressable onPress={() => setCategoryPickerVisible(true)} style={styles.categoryDropdown}>
          <View style={styles.categoryDropdownCopy}>
            <Text style={styles.categoryDropdownValue}>{category || 'All categories'}</Text>
            <Text style={styles.categoryDropdownHint}>Filter inventory dashboard and products</Text>
          </View>
          <MaterialCommunityIcons color="#52665E" name="chevron-down" size={22} />
        </Pressable>
      </View>
      {error && products.length ? <View style={styles.warning}><Text style={styles.warningText}>{error}</Text></View> : null}
      <Text style={styles.resultText}>{products.length} matching product{products.length === 1 ? '' : 's'} loaded</Text>
    </View>
  );

  const visibleCategories = categories.filter((value) => value.toLocaleLowerCase().includes(categorySearch.trim().toLocaleLowerCase()));
  return <>
    <FlatList contentContainerStyle={styles.listContent} data={products} keyExtractor={(item) => String(item.id)} ListHeaderComponent={header} ListEmptyComponent={<View style={styles.emptyState}><Text style={styles.emptyTitle}>No matching products</Text><Text style={styles.emptyText}>Try another search or stock filter.</Text></View>} ListFooterComponent={nextCursor ? <Pressable disabled={loadingMore} onPress={() => void loadMore()} style={styles.loadMore}>{loadingMore ? <ActivityIndicator color="#0B5D3B" /> : <Text style={styles.loadMoreText}>Load more products</Text>}</Pressable> : products.length ? <Text style={styles.endText}>All matching products loaded</Text> : null} refreshControl={<RefreshControl refreshing={refreshing} onRefresh={() => void loadFirst(true)} colors={['#0B5D3B']} tintColor="#0B5D3B" />} renderItem={({ item }) => <ProductCard product={item} onPress={() => setSelectedId(item.id)} />} />
    <Modal animationType="fade" transparent visible={categoryPickerVisible} onRequestClose={() => setCategoryPickerVisible(false)}>
      <View style={styles.categoryModalBackdrop}>
        <Pressable onPress={() => setCategoryPickerVisible(false)} style={styles.categoryModalDismiss} />
        <View style={styles.categoryModalCard}>
          <View style={styles.categoryModalHeader}><Text style={styles.categoryModalTitle}>Select category</Text><Pressable onPress={() => setCategoryPickerVisible(false)} style={styles.categoryModalClose}><MaterialCommunityIcons color="#52665E" name="close" size={23} /></Pressable></View>
          {categories.length > 8 ? <TextInput autoCapitalize="none" onChangeText={setCategorySearch} placeholder="Search categories" placeholderTextColor="#82958D" style={styles.categorySearchInput} value={categorySearch} /> : null}
          <ScrollView keyboardShouldPersistTaps="handled">
            {[{ code: '', label: 'All categories' }, ...visibleCategories.map((value) => ({ code: value, label: value }))].map((option) => {
              const selected = category === option.code;
              return <Pressable key={option.code || 'all-categories'} onPress={() => { setCategory(option.code); setCategorySearch(''); setCategoryPickerVisible(false); }} style={[styles.categoryOption, selected && styles.categoryOptionSelected]}><Text style={[styles.categoryOptionText, selected && styles.categoryOptionTextSelected]}>{option.label}</Text>{selected ? <MaterialCommunityIcons color="#0B5D3B" name="check" size={21} /> : null}</Pressable>;
            })}
          </ScrollView>
        </View>
      </View>
    </Modal>
  </>;
}

const styles = StyleSheet.create({
  center: { flex: 1, alignItems: 'center', justifyContent: 'center', padding: 30 }, loadingText: { color: '#587066', marginTop: 14 }, listContent: { padding: 16, paddingBottom: 28 },
  inventorySummary: { minHeight: 92, backgroundColor: '#FFFFFF', borderColor: '#DFE5E2', borderWidth: 1, borderRadius: 17, flexDirection: 'row', alignItems: 'center', padding: 14, marginBottom: 13, shadowColor: '#17352A', shadowOffset: { width: 0, height: 2 }, shadowOpacity: 0.05, shadowRadius: 6, elevation: 1 },
  summaryIcon: { width: 46, height: 46, borderRadius: 14, backgroundColor: '#E8F5EB', alignItems: 'center', justifyContent: 'center', marginRight: 11 },
  summaryCopy: { flex: 1 },
  summaryValue: { color: '#0B5D3B', fontSize: 23, fontWeight: '900' },
  summaryAttention: { color: '#D98200' },
  summaryLabel: { color: '#71867D', fontSize: 11, fontWeight: '700', marginTop: 2 },
  summaryDivider: { width: 1, height: 44, backgroundColor: '#E1E7E4', marginHorizontal: 12 },
  searchRow: { flexDirection: 'row', marginBottom: 12 }, searchInput: { flex: 1, minHeight: 50, backgroundColor: '#FFF', borderColor: '#CBD9D3', borderWidth: 1, borderRadius: 14, paddingHorizontal: 14, color: '#17352A' }, searchButton: { width: 50, height: 50, backgroundColor: '#0B5D3B', borderRadius: 14, alignItems: 'center', justifyContent: 'center', marginLeft: 8 },
  filterRow: { paddingBottom: 12, columnGap: 8 }, filterChip: { borderColor: '#CBD9D3', borderWidth: 1, borderRadius: 20, backgroundColor: '#FFF', paddingHorizontal: 14, paddingVertical: 9 }, filterActive: { backgroundColor: '#0B5D3B', borderColor: '#0B5D3B' }, filterText: { color: '#587066', fontSize: 13, fontWeight: '700' }, filterTextActive: { color: '#FFF' }, categoryFilterBlock: { marginTop: -2, marginBottom: 12 }, categoryFilterLabel: { color: '#40564D', fontSize: 12, fontWeight: '800', marginBottom: 7 }, categoryDropdown: { minHeight: 58, backgroundColor: '#FFFFFF', borderColor: '#CBD9D3', borderWidth: 1, borderRadius: 14, paddingHorizontal: 13, flexDirection: 'row', alignItems: 'center' }, categoryDropdownCopy: { flex: 1 }, categoryDropdownValue: { color: '#29483D', fontSize: 14, fontWeight: '900' }, categoryDropdownHint: { color: '#82958D', fontSize: 10, marginTop: 3 }, categoryModalBackdrop: { flex: 1, backgroundColor: 'rgba(15,35,28,.52)', justifyContent: 'center', padding: 24 }, categoryModalDismiss: { position: 'absolute', inset: 0 }, categoryModalCard: { maxHeight: '76%', backgroundColor: '#FFFFFF', borderRadius: 20, padding: 16 }, categoryModalHeader: { flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', marginBottom: 12 }, categoryModalTitle: { color: '#17352A', fontSize: 20, fontWeight: '900' }, categoryModalClose: { width: 40, height: 40, borderRadius: 20, backgroundColor: '#F1F5F3', alignItems: 'center', justifyContent: 'center' }, categorySearchInput: { minHeight: 48, borderColor: '#CBD9D3', borderWidth: 1, borderRadius: 12, paddingHorizontal: 13, color: '#17352A', marginBottom: 10 }, categoryOption: { minHeight: 50, borderBottomColor: '#E7ECEA', borderBottomWidth: 1, paddingHorizontal: 11, flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between' }, categoryOptionSelected: { backgroundColor: '#EAF6EF', borderRadius: 10 }, categoryOptionText: { color: '#40564D', fontSize: 14, fontWeight: '700' }, categoryOptionTextSelected: { color: '#0B5D3B', fontWeight: '900' }, resultText: { color: '#71867D', fontSize: 12, marginBottom: 10 },
  productCard: { minHeight: 98, backgroundColor: '#FFF', borderColor: '#DEE7E3', borderWidth: 1, borderRadius: 16, padding: 12, marginBottom: 11, flexDirection: 'row', alignItems: 'center', shadowColor: '#17352A', shadowOffset: { width: 0, height: 2 }, shadowOpacity: 0.05, shadowRadius: 5, elevation: 1 }, productImage: { width: 66, height: 66, borderRadius: 13, backgroundColor: '#EDF2EF' }, imageFallback: { width: 66, height: 66, borderRadius: 13, backgroundColor: '#E2F1E9', alignItems: 'center', justifyContent: 'center' }, imageFallbackText: { color: '#0B5D3B', fontSize: 24, fontWeight: '900' }, productCopy: { flex: 1, marginLeft: 12 }, productName: { color: '#17352A', fontSize: 15, fontWeight: '800' }, productMeta: { color: '#71867D', fontSize: 11, marginTop: 4 }, stockRow: { flexDirection: 'row', alignItems: 'center', marginTop: 8 }, stockBadge: { minHeight: 24, borderRadius: 12, backgroundColor: '#E7F6E8', paddingHorizontal: 8, flexDirection: 'row', alignItems: 'center', marginRight: 7 }, stockBadgeCritical: { backgroundColor: '#FFF0E0' }, stockDot: { width: 6, height: 6, borderRadius: 3, backgroundColor: '#147348', marginRight: 5 }, stockDotCritical: { backgroundColor: '#D98200' }, stockQuantity: { color: '#147348', fontSize: 10, fontWeight: '900' }, stockCritical: { color: '#A65A00' }, reorderText: { color: '#82958D', fontSize: 10, flex: 1 },
  warning: { backgroundColor: '#FFF4D8', borderColor: '#F0D08D', borderWidth: 1, borderRadius: 12, padding: 12, marginBottom: 12 }, warningText: { color: '#7A4A00' }, emptyState: { alignItems: 'center', paddingVertical: 48 }, emptyTitle: { color: '#17352A', fontSize: 20, fontWeight: '800' }, emptyText: { color: '#71867D', textAlign: 'center', marginTop: 7 }, loadMore: { minHeight: 50, borderColor: '#0B5D3B', borderWidth: 1, borderRadius: 14, alignItems: 'center', justifyContent: 'center' }, loadMoreText: { color: '#0B5D3B', fontWeight: '800' }, endText: { color: '#82958D', textAlign: 'center', marginVertical: 14 }, pressed: { opacity: 0.65 },
  errorTitle: { color: '#17352A', fontSize: 21, fontWeight: '800' }, errorMessage: { color: '#587066', textAlign: 'center', marginTop: 8 }, primaryButton: { backgroundColor: '#0B5D3B', minHeight: 48, borderRadius: 13, paddingHorizontal: 24, alignItems: 'center', justifyContent: 'center', marginTop: 20 }, primaryText: { color: '#FFF', fontWeight: '800' },
  detailContent: { padding: 16, paddingBottom: 32 }, backButton: { minHeight: 46, backgroundColor: '#FFFFFF', borderColor: '#DCE5E1', borderWidth: 1, borderRadius: 13, paddingHorizontal: 13, flexDirection: 'row', alignItems: 'center', alignSelf: 'stretch', marginBottom: 10, columnGap: 8 }, backText: { color: '#0B5D3B', fontWeight: '800' }, heroCard: { backgroundColor: '#FFF', borderColor: '#DFE7E3', borderWidth: 1, borderRadius: 18, padding: 18, marginBottom: 22, shadowColor: '#17352A', shadowOffset: { width: 0, height: 2 }, shadowOpacity: 0.05, shadowRadius: 6, elevation: 1 }, heroName: { color: '#17352A', fontSize: 22, fontWeight: '900' }, heroSku: { color: '#71867D', marginTop: 5 }, quantityPanel: { backgroundColor: '#E4F3EB', borderRadius: 14, padding: 14, marginTop: 16, flexDirection: 'row', alignItems: 'center' }, quantityValue: { color: '#0B5D3B', fontSize: 34, fontWeight: '900', marginRight: 14 }, quantityLabel: { color: '#174E36', fontWeight: '800' }, adjustStockButton: { minHeight: 48, backgroundColor: '#0B5D3B', borderRadius: 13, marginTop: 12, flexDirection: 'row', alignItems: 'center', justifyContent: 'center', columnGap: 8 }, adjustStockText: { color: '#FFFFFF', fontWeight: '900' }, sectionTitle: { color: '#17352A', fontSize: 18, fontWeight: '800', marginBottom: 10 }, sectionCard: { backgroundColor: '#FFF', borderColor: '#E0E7E3', borderWidth: 1, borderRadius: 17, padding: 16, marginBottom: 22 }, detailRow: { marginBottom: 13 }, detailLabel: { color: '#71867D', fontSize: 11, fontWeight: '800', textTransform: 'uppercase' }, detailValue: { color: '#29483D', marginTop: 4, lineHeight: 20 },
  movementRow: { flexDirection: 'row', paddingVertical: 5 }, divider: { borderTopColor: '#E7ECEA', borderTopWidth: 1, paddingTop: 13, marginTop: 7 }, deltaBadge: { width: 45, height: 34, borderRadius: 10, backgroundColor: '#E4F3EB', alignItems: 'center', justifyContent: 'center', marginRight: 11 }, deltaNegative: { backgroundColor: '#FDE8E7' }, deltaText: { color: '#147348', fontWeight: '900' }, deltaTextNegative: { color: '#B42318' }, movementCopy: { flex: 1 }, movementTitle: { color: '#29483D', fontWeight: '800' }, movementNote: { color: '#587066', marginTop: 3 }, movementTime: { color: '#82958D', fontSize: 11, marginTop: 5 },
  modalKeyboardView: { flex: 1 }, modalBackdrop: { flex: 1, backgroundColor: 'rgba(15, 35, 28, 0.52)', justifyContent: 'flex-end' }, quantityModal: { maxHeight: '88%', backgroundColor: '#FFFFFF', borderTopLeftRadius: 24, borderTopRightRadius: 24, paddingHorizontal: 20, paddingTop: 18, paddingBottom: 10 }, quantityModalScroll: { paddingBottom: 22 }, modalHeader: { flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', marginBottom: 18 }, modalTitleWrap: { flex: 1, paddingRight: 12 }, modalTitle: { color: '#17352A', fontSize: 20, fontWeight: '900' }, modalSubtitle: { color: '#71867D', fontSize: 12, marginTop: 4 }, modalClose: { width: 42, height: 42, borderRadius: 21, backgroundColor: '#F1F5F3', alignItems: 'center', justifyContent: 'center' }, formLabel: { color: '#29483D', fontSize: 13, fontWeight: '800', marginBottom: 7 }, formInput: { minHeight: 49, borderColor: '#CBD9D3', borderWidth: 1, borderRadius: 12, backgroundColor: '#FFFFFF', color: '#17352A', fontSize: 15, paddingHorizontal: 14 }, formHint: { color: '#71867D', fontSize: 11, marginTop: 6 }, noteLabel: { marginTop: 18 }, noteInput: { minHeight: 86, paddingTop: 12, textAlignVertical: 'top' }, quantityError: { color: '#B42318', backgroundColor: '#FFF2F0', borderRadius: 10, padding: 11, lineHeight: 18, marginTop: 14 }, saveQuantityButton: { minHeight: 52, borderRadius: 14, backgroundColor: '#0B5D3B', alignItems: 'center', justifyContent: 'center', marginTop: 18 }, saveQuantityText: { color: '#FFFFFF', fontSize: 15, fontWeight: '900' }, disabledButton: { opacity: 0.42 },
});
