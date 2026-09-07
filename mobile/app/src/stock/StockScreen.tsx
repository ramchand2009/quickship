import { useCallback, useEffect, useState } from 'react';
import MaterialCommunityIcons from '@expo/vector-icons/MaterialCommunityIcons';
import * as Print from 'expo-print';
import * as Sharing from 'expo-sharing';
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
const priceDraft = (value: Money | null) => value?.amount ?? '';
const when = (value: string) => {
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? '' : date.toLocaleString([], { day: '2-digit', month: 'short', hour: '2-digit', minute: '2-digit' });
};
const newIdempotencyKey = () => `android-${Date.now()}-${Math.random().toString(36).slice(2, 12)}`;

const CODE128_PATTERNS = [
  '212222', '222122', '222221', '121223', '121322', '131222', '122213', '122312', '132212', '221213',
  '221312', '231212', '112232', '122132', '122231', '113222', '123122', '123221', '223211', '221132',
  '221231', '213212', '223112', '312131', '311222', '321122', '321221', '312212', '322112', '322211',
  '212123', '212321', '232121', '111323', '131123', '131321', '112313', '132113', '132311', '211313',
  '231113', '231311', '112133', '112331', '132131', '113123', '113321', '133121', '313121', '211331',
  '231131', '213113', '213311', '213131', '311123', '311321', '331121', '312113', '312311', '332111',
  '314111', '221411', '431111', '111224', '111422', '121124', '121421', '141122', '141221', '112214',
  '112412', '122114', '122411', '142112', '142211', '241211', '221114', '413111', '241112', '134111',
  '111242', '121142', '121241', '114212', '124112', '124211', '411212', '421112', '421211', '212141',
  '214121', '412121', '111143', '111341', '131141', '114113', '114311', '411113', '411311', '113141',
  '114131', '311141', '411131', '211412', '211214', '211232', '2331112',
] as const;

const escapeHtml = (value: string) => value
  .replace(/&/g, '&amp;')
  .replace(/</g, '&lt;')
  .replace(/>/g, '&gt;')
  .replace(/"/g, '&quot;')
  .replace(/'/g, '&#39;');

const barcodeValue = (product: ProductDetail) => (product.barcode || product.sku || '').trim();

const code128BarcodeSvg = (value: string) => {
  const safeValue = value.split('').filter((character) => {
    const code = character.charCodeAt(0);
    return code >= 32 && code <= 126;
  }).join('');
  if (!safeValue) return '';

  const codes = [104];
  let checksum = 104;
  safeValue.split('').forEach((character, index) => {
    const code = character.charCodeAt(0) - 32;
    codes.push(code);
    checksum += code * (index + 1);
  });
  codes.push(checksum % 103, 106);

  const quietZone = 10;
  const height = 58;
  let x = quietZone;
  const bars: string[] = [];
  codes.forEach((code) => {
    const pattern = CODE128_PATTERNS[code];
    pattern.split('').forEach((widthText, index) => {
      const width = Number(widthText);
      if (index % 2 === 0) {
        bars.push(`<rect x="${x}" y="0" width="${width}" height="${height}" fill="#000"/>`);
      }
      x += width;
    });
  });
  const totalWidth = x + quietZone;
  return `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 ${totalWidth} ${height}" preserveAspectRatio="none">${bars.join('')}</svg>`;
};

const barcodeLabelHtml = (product: ProductDetail) => {
  const code = barcodeValue(product);
  const title = product.name.trim().toUpperCase();
  const barcodeSvg = code128BarcodeSvg(code);
  return `<!doctype html>
<html>
<head>
  <meta charset="utf-8" />
  <style>
    @page { size: 50mm 25mm; margin: 0; }
    * { box-sizing: border-box; }
    html, body { width: 50mm; height: 25mm; margin: 0; padding: 0; background: #fff; }
    body { font-family: Arial, Helvetica, sans-serif; color: #000; }
    .label {
      width: 50mm;
      height: 25mm;
      padding: 2.2mm 3mm 1.6mm;
      display: flex;
      flex-direction: column;
      align-items: center;
      justify-content: center;
      overflow: hidden;
      background: #fff;
    }
    .title {
      width: 100%;
      font-size: 13px;
      line-height: 1;
      font-weight: 900;
      text-align: center;
      white-space: nowrap;
      overflow: hidden;
      text-overflow: ellipsis;
      letter-spacing: .2px;
      margin-bottom: 1.4mm;
    }
    .barcode {
      width: 44mm;
      height: 10.2mm;
      margin-bottom: .8mm;
    }
    .barcode svg { width: 100%; height: 100%; display: block; }
    .code {
      width: 100%;
      font-size: 14px;
      line-height: 1;
      font-weight: 700;
      text-align: center;
      white-space: nowrap;
      overflow: hidden;
      text-overflow: ellipsis;
      letter-spacing: .8px;
    }
  </style>
</head>
<body>
  <div class="label">
    <div class="title">${escapeHtml(title)}</div>
    <div class="barcode">${barcodeSvg}</div>
    <div class="code">${escapeHtml(code)}</div>
  </div>
</body>
</html>`;
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
  const [quantityEditorVisible, setQuantityEditorVisible] = useState(false);
  const [targetQuantity, setTargetQuantity] = useState('');
  const [adjustmentNote, setAdjustmentNote] = useState('');
  const [quantitySaving, setQuantitySaving] = useState(false);
  const [quantityError, setQuantityError] = useState('');
  const [labelSaving, setLabelSaving] = useState(false);
  const [productEditorVisible, setProductEditorVisible] = useState(false);
  const [productSaving, setProductSaving] = useState(false);
  const [productError, setProductError] = useState('');
  const [productDraft, setProductDraft] = useState({
    name: '',
    sku: '',
    barcode: '',
    category: '',
    description: '',
    actual_price: '',
    regular_price: '',
    sale_price: '',
    reorder_level: '',
    is_active: true,
  });

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

  const openProductEditor = () => {
    setProductDraft({
      name: product.name,
      sku: product.sku,
      barcode: product.barcode ?? '',
      category: product.category ?? '',
      description: product.description ?? '',
      actual_price: priceDraft(product.prices.actual),
      regular_price: priceDraft(product.prices.regular),
      sale_price: priceDraft(product.prices.sale),
      reorder_level: String(product.reorder_level),
      is_active: product.is_active,
    });
    setProductError('');
    setProductEditorVisible(true);
  };

  const updateProductDraft = (key: keyof typeof productDraft, value: string | boolean) => {
    setProductDraft((current) => ({ ...current, [key]: value }));
  };

  const quantityReady = /^\d+$/.test(targetQuantity.trim()) && Number(targetQuantity) <= 999999999;
  const priceReady = (value: string) => value.trim() === '' || /^\d+(\.\d{0,2})?$/.test(value.trim());
  const productReady = Boolean(productDraft.name.trim())
    && Boolean(productDraft.sku.trim())
    && /^\d+$/.test(productDraft.reorder_level.trim())
    && Number(productDraft.reorder_level) <= 999999999
    && priceReady(productDraft.actual_price)
    && priceReady(productDraft.regular_price)
    && priceReady(productDraft.sale_price);
  const canCreateBarcodeLabel = Boolean(barcodeValue(product));

  const saveBarcodeLabel = async () => {
    const code = barcodeValue(product);
    if (!code || labelSaving) {
      Alert.alert('Barcode unavailable', 'Add a barcode or SKU for this product before creating the label.');
      return;
    }
    setLabelSaving(true);
    try {
      const available = await Sharing.isAvailableAsync();
      if (!available) {
        Alert.alert('Sharing unavailable', 'This mobile does not support saving or sharing PDF files from the app.');
        return;
      }
      const pdf = await Print.printToFileAsync({
        html: barcodeLabelHtml(product),
        width: 142,
        height: 71,
      });
      await Sharing.shareAsync(pdf.uri, {
        dialogTitle: `${product.name} barcode label`,
        mimeType: 'application/pdf',
        UTI: 'com.adobe.pdf',
      });
    } catch {
      Alert.alert('Label not created', 'The barcode label PDF could not be created. Please try again.');
    } finally {
      setLabelSaving(false);
    }
  };

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

  const submitProduct = async () => {
    if (!productReady || productSaving) return;
    setProductSaving(true);
    setProductError('');
    const optionalPrice = (value: string) => value.trim() ? Number(value.trim()).toFixed(2) : null;
    try {
      const response = await runAuthenticated((token) => api.updateProduct(
        token,
        product.id,
        {
          expected_updated_at: product.updated_at,
          name: productDraft.name.trim(),
          sku: productDraft.sku.trim(),
          barcode: productDraft.barcode.trim() || null,
          category: productDraft.category.trim(),
          description: productDraft.description.trim(),
          actual_price: optionalPrice(productDraft.actual_price),
          regular_price: optionalPrice(productDraft.regular_price),
          sale_price: optionalPrice(productDraft.sale_price),
          reorder_level: Number(productDraft.reorder_level),
          is_active: productDraft.is_active,
        },
        newIdempotencyKey(),
      ));
      setProduct(response.data.product);
      setProductEditorVisible(false);
      const wooEffect = response.data.effects?.find((effect) => effect.code === 'woocommerce_sync');
      const wooMessage = wooEffect?.message ? `\n\n${wooEffect.message}` : '';
      Alert.alert('Product updated', `Product details saved.${wooMessage}`);
    } catch (reason) {
      if (reason instanceof api.ApiError && reason.status === 409) {
        setProductEditorVisible(false);
        await load(true);
        Alert.alert('Product was refreshed', 'The product changed before your update was saved. Review it and try again.');
      } else {
        setProductError(reason instanceof api.ApiError ? reason.message : 'Product details could not be updated.');
      }
    } finally {
      setProductSaving(false);
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
        {product.can_edit_product ? (
          <Pressable onPress={openProductEditor} style={({ pressed }) => [styles.editProductButton, pressed && styles.pressed]}>
            <MaterialCommunityIcons color="#0B5D3B" name="store-edit-outline" size={20} />
            <Text style={styles.editProductText}>Edit product details</Text>
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
        <Pressable
          disabled={!canCreateBarcodeLabel || labelSaving}
          onPress={() => void saveBarcodeLabel()}
          style={({ pressed }) => [
            styles.barcodeLabelButton,
            (!canCreateBarcodeLabel || labelSaving) && styles.disabledButton,
            pressed && styles.pressed,
          ]}
        >
          <MaterialCommunityIcons color="#0B5D3B" name="barcode" size={22} />
          <View style={styles.barcodeLabelCopy}>
            <Text style={styles.barcodeLabelTitle}>{labelSaving ? 'Creating label PDF...' : 'Save barcode label PDF'}</Text>
            <Text style={styles.barcodeLabelHint}>50 × 25 mm original size for Label Expert</Text>
          </View>
          {labelSaving ? <ActivityIndicator color="#0B5D3B" /> : <MaterialCommunityIcons color="#63766E" name="share-variant-outline" size={21} />}
        </Pressable>
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
    <Modal
      animationType="slide"
      onRequestClose={() => !productSaving && setProductEditorVisible(false)}
      transparent
      visible={productEditorVisible}
    >
      <KeyboardAvoidingView behavior={Platform.OS === 'ios' ? 'padding' : 'height'} style={styles.modalKeyboardView}>
        <View style={styles.modalBackdrop}>
          <View style={styles.productModal}>
            <View style={styles.modalHeader}>
              <View style={styles.modalTitleWrap}>
                <Text style={styles.modalTitle}>Edit product</Text>
                <Text style={styles.modalSubtitle}>Update product master details</Text>
              </View>
              <Pressable disabled={productSaving} onPress={() => setProductEditorVisible(false)} style={styles.modalClose}>
                <MaterialCommunityIcons color="#587066" name="close" size={24} />
              </Pressable>
            </View>
            <ScrollView
              contentContainerStyle={styles.quantityModalScroll}
              keyboardDismissMode="on-drag"
              keyboardShouldPersistTaps="handled"
              showsVerticalScrollIndicator={false}
            >
              <Text style={styles.formLabel}>Product name</Text>
              <TextInput maxLength={160} onChangeText={(value) => updateProductDraft('name', value)} placeholder="Product name" placeholderTextColor="#82958D" style={styles.formInput} value={productDraft.name} />

              <View style={styles.twoColumnRow}>
                <View style={styles.twoColumnField}>
                  <Text style={styles.formLabel}>SKU</Text>
                  <TextInput autoCapitalize="characters" maxLength={120} onChangeText={(value) => updateProductDraft('sku', value)} placeholder="SKU" placeholderTextColor="#82958D" style={styles.formInput} value={productDraft.sku} />
                </View>
                <View style={styles.twoColumnField}>
                  <Text style={styles.formLabel}>Barcode</Text>
                  <TextInput autoCapitalize="characters" maxLength={120} onChangeText={(value) => updateProductDraft('barcode', value)} placeholder="Barcode" placeholderTextColor="#82958D" style={styles.formInput} value={productDraft.barcode} />
                </View>
              </View>

              <Text style={[styles.formLabel, styles.noteLabel]}>Category</Text>
              <TextInput maxLength={120} onChangeText={(value) => updateProductDraft('category', value)} placeholder="Category" placeholderTextColor="#82958D" style={styles.formInput} value={productDraft.category} />

              <View style={styles.twoColumnRow}>
                <View style={styles.twoColumnField}>
                  <Text style={styles.formLabel}>Purchase price</Text>
                  <TextInput keyboardType="decimal-pad" onChangeText={(value) => updateProductDraft('actual_price', value)} placeholder="0.00" placeholderTextColor="#82958D" style={styles.formInput} value={productDraft.actual_price} />
                </View>
                <View style={styles.twoColumnField}>
                  <Text style={styles.formLabel}>Regular price</Text>
                  <TextInput keyboardType="decimal-pad" onChangeText={(value) => updateProductDraft('regular_price', value)} placeholder="0.00" placeholderTextColor="#82958D" style={styles.formInput} value={productDraft.regular_price} />
                </View>
              </View>

              <View style={styles.twoColumnRow}>
                <View style={styles.twoColumnField}>
                  <Text style={styles.formLabel}>Sale price</Text>
                  <TextInput keyboardType="decimal-pad" onChangeText={(value) => updateProductDraft('sale_price', value)} placeholder="0.00" placeholderTextColor="#82958D" style={styles.formInput} value={productDraft.sale_price} />
                </View>
                <View style={styles.twoColumnField}>
                  <Text style={styles.formLabel}>Reorder level</Text>
                  <TextInput keyboardType="number-pad" maxLength={9} onChangeText={(value) => updateProductDraft('reorder_level', value)} placeholder="0" placeholderTextColor="#82958D" style={styles.formInput} value={productDraft.reorder_level} />
                </View>
              </View>

              <Text style={[styles.formLabel, styles.noteLabel]}>Description</Text>
              <TextInput
                maxLength={5000}
                multiline
                onChangeText={(value) => updateProductDraft('description', value)}
                placeholder="Product description"
                placeholderTextColor="#82958D"
                style={[styles.formInput, styles.noteInput]}
                value={productDraft.description}
              />

              <Pressable onPress={() => updateProductDraft('is_active', !productDraft.is_active)} style={styles.activeToggleRow}>
                <View style={[styles.activeToggleIcon, productDraft.is_active && styles.activeToggleOn]}>
                  <MaterialCommunityIcons color={productDraft.is_active ? '#FFFFFF' : '#71867D'} name={productDraft.is_active ? 'check' : 'close'} size={18} />
                </View>
                <View style={styles.activeToggleCopy}>
                  <Text style={styles.activeToggleTitle}>{productDraft.is_active ? 'Product active' : 'Product inactive'}</Text>
                  <Text style={styles.activeToggleHint}>Inactive products are saved as draft in WooCommerce.</Text>
                </View>
              </Pressable>

              {productError ? <Text accessibilityRole="alert" style={styles.quantityError}>{productError}</Text> : null}
              <Pressable
                disabled={!productReady || productSaving}
                onPress={() => void submitProduct()}
                style={[styles.saveQuantityButton, (!productReady || productSaving) && styles.disabledButton]}
              >
                {productSaving ? <ActivityIndicator color="#FFFFFF" /> : <Text style={styles.saveQuantityText}>Save product</Text>}
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
  detailContent: { padding: 16, paddingBottom: 32 }, backButton: { minHeight: 46, backgroundColor: '#FFFFFF', borderColor: '#DCE5E1', borderWidth: 1, borderRadius: 13, paddingHorizontal: 13, flexDirection: 'row', alignItems: 'center', alignSelf: 'stretch', marginBottom: 10, columnGap: 8 }, backText: { color: '#0B5D3B', fontWeight: '800' }, heroCard: { backgroundColor: '#FFF', borderColor: '#DFE7E3', borderWidth: 1, borderRadius: 18, padding: 18, marginBottom: 22, shadowColor: '#17352A', shadowOffset: { width: 0, height: 2 }, shadowOpacity: 0.05, shadowRadius: 6, elevation: 1 }, heroName: { color: '#17352A', fontSize: 22, fontWeight: '900' }, heroSku: { color: '#71867D', marginTop: 5 }, quantityPanel: { backgroundColor: '#E4F3EB', borderRadius: 14, padding: 14, marginTop: 16, flexDirection: 'row', alignItems: 'center' }, quantityValue: { color: '#0B5D3B', fontSize: 34, fontWeight: '900', marginRight: 14 }, quantityLabel: { color: '#174E36', fontWeight: '800' }, adjustStockButton: { minHeight: 48, backgroundColor: '#0B5D3B', borderRadius: 13, marginTop: 12, flexDirection: 'row', alignItems: 'center', justifyContent: 'center', columnGap: 8 }, adjustStockText: { color: '#FFFFFF', fontWeight: '900' }, editProductButton: { minHeight: 48, backgroundColor: '#F4FBF7', borderColor: '#BFD8CA', borderWidth: 1, borderRadius: 13, marginTop: 10, flexDirection: 'row', alignItems: 'center', justifyContent: 'center', columnGap: 8 }, editProductText: { color: '#0B5D3B', fontWeight: '900' }, sectionTitle: { color: '#17352A', fontSize: 18, fontWeight: '800', marginBottom: 10 }, sectionCard: { backgroundColor: '#FFF', borderColor: '#E0E7E3', borderWidth: 1, borderRadius: 17, padding: 16, marginBottom: 22 }, detailRow: { marginBottom: 13 }, detailLabel: { color: '#71867D', fontSize: 11, fontWeight: '800', textTransform: 'uppercase' }, detailValue: { color: '#29483D', marginTop: 4, lineHeight: 20 }, barcodeLabelButton: { minHeight: 64, borderColor: '#BFD8CA', borderWidth: 1, borderRadius: 15, backgroundColor: '#F4FBF7', paddingHorizontal: 13, paddingVertical: 11, flexDirection: 'row', alignItems: 'center', columnGap: 11, marginTop: 2 }, barcodeLabelCopy: { flex: 1 }, barcodeLabelTitle: { color: '#0B5D3B', fontSize: 14, fontWeight: '900' }, barcodeLabelHint: { color: '#71867D', fontSize: 11, marginTop: 3 },
  movementRow: { flexDirection: 'row', paddingVertical: 5 }, divider: { borderTopColor: '#E7ECEA', borderTopWidth: 1, paddingTop: 13, marginTop: 7 }, deltaBadge: { width: 45, height: 34, borderRadius: 10, backgroundColor: '#E4F3EB', alignItems: 'center', justifyContent: 'center', marginRight: 11 }, deltaNegative: { backgroundColor: '#FDE8E7' }, deltaText: { color: '#147348', fontWeight: '900' }, deltaTextNegative: { color: '#B42318' }, movementCopy: { flex: 1 }, movementTitle: { color: '#29483D', fontWeight: '800' }, movementNote: { color: '#587066', marginTop: 3 }, movementTime: { color: '#82958D', fontSize: 11, marginTop: 5 },
  modalKeyboardView: { flex: 1 }, modalBackdrop: { flex: 1, backgroundColor: 'rgba(15, 35, 28, 0.52)', justifyContent: 'flex-end' }, quantityModal: { maxHeight: '88%', backgroundColor: '#FFFFFF', borderTopLeftRadius: 24, borderTopRightRadius: 24, paddingHorizontal: 20, paddingTop: 18, paddingBottom: 10 }, productModal: { maxHeight: '93%', backgroundColor: '#FFFFFF', borderTopLeftRadius: 24, borderTopRightRadius: 24, paddingHorizontal: 20, paddingTop: 18, paddingBottom: 10 }, quantityModalScroll: { paddingBottom: 22 }, modalHeader: { flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', marginBottom: 18 }, modalTitleWrap: { flex: 1, paddingRight: 12 }, modalTitle: { color: '#17352A', fontSize: 20, fontWeight: '900' }, modalSubtitle: { color: '#71867D', fontSize: 12, marginTop: 4 }, modalClose: { width: 42, height: 42, borderRadius: 21, backgroundColor: '#F1F5F3', alignItems: 'center', justifyContent: 'center' }, formLabel: { color: '#29483D', fontSize: 13, fontWeight: '800', marginBottom: 7 }, formInput: { minHeight: 49, borderColor: '#CBD9D3', borderWidth: 1, borderRadius: 12, backgroundColor: '#FFFFFF', color: '#17352A', fontSize: 15, paddingHorizontal: 14 }, formHint: { color: '#71867D', fontSize: 11, marginTop: 6 }, noteLabel: { marginTop: 18 }, noteInput: { minHeight: 86, paddingTop: 12, textAlignVertical: 'top' }, twoColumnRow: { flexDirection: 'row', columnGap: 10, marginTop: 16 }, twoColumnField: { flex: 1 }, activeToggleRow: { minHeight: 64, borderColor: '#DCE5E1', borderWidth: 1, borderRadius: 14, padding: 12, marginTop: 16, flexDirection: 'row', alignItems: 'center', backgroundColor: '#F8FBF9' }, activeToggleIcon: { width: 34, height: 34, borderRadius: 17, backgroundColor: '#E6ECE9', alignItems: 'center', justifyContent: 'center', marginRight: 11 }, activeToggleOn: { backgroundColor: '#0B5D3B' }, activeToggleCopy: { flex: 1 }, activeToggleTitle: { color: '#17352A', fontWeight: '900' }, activeToggleHint: { color: '#71867D', fontSize: 11, marginTop: 3 }, quantityError: { color: '#B42318', backgroundColor: '#FFF2F0', borderRadius: 10, padding: 11, lineHeight: 18, marginTop: 14 }, saveQuantityButton: { minHeight: 52, borderRadius: 14, backgroundColor: '#0B5D3B', alignItems: 'center', justifyContent: 'center', marginTop: 18 }, saveQuantityText: { color: '#FFFFFF', fontSize: 15, fontWeight: '900' }, disabledButton: { opacity: 0.42 },
});
