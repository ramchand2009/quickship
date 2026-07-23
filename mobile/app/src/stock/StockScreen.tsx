import { useCallback, useEffect, useState } from 'react';
import MaterialCommunityIcons from '@expo/vector-icons/MaterialCommunityIcons';
import { ActivityIndicator, FlatList, Image, Pressable, RefreshControl, ScrollView, StyleSheet, Text, TextInput, View } from 'react-native';

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

  return (
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

      {product.prices.actual || product.prices.regular || product.prices.sale ? <><Text style={styles.sectionTitle}>Prices</Text><View style={styles.sectionCard}><DetailRow label="Actual" value={money(product.prices.actual)} /><DetailRow label="Regular" value={money(product.prices.regular)} /><DetailRow label="Sale" value={money(product.prices.sale)} /></View></> : null}

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
  );
}

export default function StockScreen() {
  const { runAuthenticated } = useAuth();
  const [products, setProducts] = useState<ProductSummary[]>([]);
  const [draftSearch, setDraftSearch] = useState('');
  const [search, setSearch] = useState('');
  const [stockState, setStockState] = useState('');
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
      const response = await runAuthenticated((token) => api.products(token, { search, stock_state: stockState }));
      setProducts(response.data); setNextCursor(response.pagination.next_cursor);
    } catch (reason) { setError(reason instanceof api.ApiError ? reason.message : 'Stock could not be loaded.'); }
    finally { setLoading(false); setRefreshing(false); }
  }, [runAuthenticated, search, stockState]);
  useEffect(() => { void loadFirst(); }, [loadFirst]);

  const loadMore = async () => {
    if (!nextCursor || loadingMore) return;
    setLoadingMore(true);
    try {
      const response = await runAuthenticated((token) => api.products(token, { search, stock_state: stockState, cursor: nextCursor }));
      setProducts((current) => [...current, ...response.data]); setNextCursor(response.pagination.next_cursor);
    } catch (reason) { setError(reason instanceof api.ApiError ? reason.message : 'More products could not be loaded.'); }
    finally { setLoadingMore(false); }
  };

  if (selectedId !== null) return <ProductDetailScreen productId={selectedId} onBack={() => setSelectedId(null)} />;
  if (loading && !products.length) return <View style={styles.center}><ActivityIndicator size="large" color="#0B5D3B" /><Text style={styles.loadingText}>Loading stock...</Text></View>;
  if (error && !products.length) return <View style={styles.center}><Text style={styles.errorTitle}>Stock unavailable</Text><Text style={styles.errorMessage}>{error}</Text><Pressable onPress={() => void loadFirst()} style={styles.primaryButton}><Text style={styles.primaryText}>Try again</Text></Pressable></View>;

  const attentionCount = products.filter((product) => product.stock_state !== 'in_stock').length;
  const header = (
    <View>
      <View style={styles.inventorySummary}>
        <View style={styles.summaryIcon}><MaterialCommunityIcons color="#14733D" name="package-variant-closed" size={26} /></View>
        <View style={styles.summaryCopy}>
          <Text style={styles.summaryValue}>{products.length}</Text>
          <Text style={styles.summaryLabel}>Products loaded</Text>
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
      {error && products.length ? <View style={styles.warning}><Text style={styles.warningText}>{error}</Text></View> : null}
      <Text style={styles.resultText}>{products.length} product{products.length === 1 ? '' : 's'} loaded</Text>
    </View>
  );

  return <FlatList contentContainerStyle={styles.listContent} data={products} keyExtractor={(item) => String(item.id)} ListHeaderComponent={header} ListEmptyComponent={<View style={styles.emptyState}><Text style={styles.emptyTitle}>No matching products</Text><Text style={styles.emptyText}>Try another search or stock filter.</Text></View>} ListFooterComponent={nextCursor ? <Pressable disabled={loadingMore} onPress={() => void loadMore()} style={styles.loadMore}>{loadingMore ? <ActivityIndicator color="#0B5D3B" /> : <Text style={styles.loadMoreText}>Load more products</Text>}</Pressable> : products.length ? <Text style={styles.endText}>All matching products loaded</Text> : null} refreshControl={<RefreshControl refreshing={refreshing} onRefresh={() => void loadFirst(true)} colors={['#0B5D3B']} tintColor="#0B5D3B" />} renderItem={({ item }) => <ProductCard product={item} onPress={() => setSelectedId(item.id)} />} />;
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
  filterRow: { paddingBottom: 12, columnGap: 8 }, filterChip: { borderColor: '#CBD9D3', borderWidth: 1, borderRadius: 20, backgroundColor: '#FFF', paddingHorizontal: 14, paddingVertical: 9 }, filterActive: { backgroundColor: '#0B5D3B', borderColor: '#0B5D3B' }, filterText: { color: '#587066', fontSize: 13, fontWeight: '700' }, filterTextActive: { color: '#FFF' }, resultText: { color: '#71867D', fontSize: 12, marginBottom: 10 },
  productCard: { minHeight: 98, backgroundColor: '#FFF', borderColor: '#DEE7E3', borderWidth: 1, borderRadius: 16, padding: 12, marginBottom: 11, flexDirection: 'row', alignItems: 'center', shadowColor: '#17352A', shadowOffset: { width: 0, height: 2 }, shadowOpacity: 0.05, shadowRadius: 5, elevation: 1 }, productImage: { width: 66, height: 66, borderRadius: 13, backgroundColor: '#EDF2EF' }, imageFallback: { width: 66, height: 66, borderRadius: 13, backgroundColor: '#E2F1E9', alignItems: 'center', justifyContent: 'center' }, imageFallbackText: { color: '#0B5D3B', fontSize: 24, fontWeight: '900' }, productCopy: { flex: 1, marginLeft: 12 }, productName: { color: '#17352A', fontSize: 15, fontWeight: '800' }, productMeta: { color: '#71867D', fontSize: 11, marginTop: 4 }, stockRow: { flexDirection: 'row', alignItems: 'center', marginTop: 8 }, stockBadge: { minHeight: 24, borderRadius: 12, backgroundColor: '#E7F6E8', paddingHorizontal: 8, flexDirection: 'row', alignItems: 'center', marginRight: 7 }, stockBadgeCritical: { backgroundColor: '#FFF0E0' }, stockDot: { width: 6, height: 6, borderRadius: 3, backgroundColor: '#147348', marginRight: 5 }, stockDotCritical: { backgroundColor: '#D98200' }, stockQuantity: { color: '#147348', fontSize: 10, fontWeight: '900' }, stockCritical: { color: '#A65A00' }, reorderText: { color: '#82958D', fontSize: 10, flex: 1 },
  warning: { backgroundColor: '#FFF4D8', borderColor: '#F0D08D', borderWidth: 1, borderRadius: 12, padding: 12, marginBottom: 12 }, warningText: { color: '#7A4A00' }, emptyState: { alignItems: 'center', paddingVertical: 48 }, emptyTitle: { color: '#17352A', fontSize: 20, fontWeight: '800' }, emptyText: { color: '#71867D', textAlign: 'center', marginTop: 7 }, loadMore: { minHeight: 50, borderColor: '#0B5D3B', borderWidth: 1, borderRadius: 14, alignItems: 'center', justifyContent: 'center' }, loadMoreText: { color: '#0B5D3B', fontWeight: '800' }, endText: { color: '#82958D', textAlign: 'center', marginVertical: 14 }, pressed: { opacity: 0.65 },
  errorTitle: { color: '#17352A', fontSize: 21, fontWeight: '800' }, errorMessage: { color: '#587066', textAlign: 'center', marginTop: 8 }, primaryButton: { backgroundColor: '#0B5D3B', minHeight: 48, borderRadius: 13, paddingHorizontal: 24, alignItems: 'center', justifyContent: 'center', marginTop: 20 }, primaryText: { color: '#FFF', fontWeight: '800' },
  detailContent: { padding: 18, paddingBottom: 32 }, backButton: { minHeight: 46, backgroundColor: '#FFFFFF', borderBottomColor: '#DCE5E1', borderBottomWidth: 1, borderRadius: 12, paddingHorizontal: 13, flexDirection: 'row', alignItems: 'center', alignSelf: 'stretch', marginBottom: 8, columnGap: 8 }, backText: { color: '#0B5D3B', fontWeight: '800' }, heroCard: { backgroundColor: '#FFF', borderRadius: 18, padding: 18, marginBottom: 22 }, heroName: { color: '#17352A', fontSize: 22, fontWeight: '900' }, heroSku: { color: '#71867D', marginTop: 5 }, quantityPanel: { backgroundColor: '#E4F3EB', borderRadius: 14, padding: 14, marginTop: 16, flexDirection: 'row', alignItems: 'center' }, quantityValue: { color: '#0B5D3B', fontSize: 34, fontWeight: '900', marginRight: 14 }, quantityLabel: { color: '#174E36', fontWeight: '800' }, sectionTitle: { color: '#17352A', fontSize: 18, fontWeight: '800', marginBottom: 10 }, sectionCard: { backgroundColor: '#FFF', borderColor: '#E0E7E3', borderWidth: 1, borderRadius: 16, padding: 16, marginBottom: 22 }, detailRow: { marginBottom: 13 }, detailLabel: { color: '#71867D', fontSize: 11, fontWeight: '800', textTransform: 'uppercase' }, detailValue: { color: '#29483D', marginTop: 4, lineHeight: 20 },
  movementRow: { flexDirection: 'row', paddingVertical: 5 }, divider: { borderTopColor: '#E7ECEA', borderTopWidth: 1, paddingTop: 13, marginTop: 7 }, deltaBadge: { width: 45, height: 34, borderRadius: 10, backgroundColor: '#E4F3EB', alignItems: 'center', justifyContent: 'center', marginRight: 11 }, deltaNegative: { backgroundColor: '#FDE8E7' }, deltaText: { color: '#147348', fontWeight: '900' }, deltaTextNegative: { color: '#B42318' }, movementCopy: { flex: 1 }, movementTitle: { color: '#29483D', fontWeight: '800' }, movementNote: { color: '#587066', marginTop: 3 }, movementTime: { color: '#82958D', fontSize: 11, marginTop: 5 },
});
