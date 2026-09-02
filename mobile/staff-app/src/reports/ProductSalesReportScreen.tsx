import { useCallback, useEffect, useState } from 'react';
import MaterialCommunityIcons from '@expo/vector-icons/MaterialCommunityIcons';
import { ActivityIndicator, Pressable, RefreshControl, ScrollView, StyleSheet, Text, View } from 'react-native';

import * as api from '../auth/api';
import { useAuth } from '../auth/AuthContext';
import type { Money } from '../orders/types';
import type { ProductSalesReportResponse } from './types';

const monthKey = (date: Date) => `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, '0')}`;

function moveMonth(value: string, amount: number) {
  const [year, month] = value.split('-').map(Number);
  return monthKey(new Date(year, month - 1 + amount, 1));
}

function money(value: Money) {
  return `${value.currency === 'INR' ? '₹' : value.currency} ${value.amount}`;
}

export default function ProductSalesReportScreen({ initialMonth, onBack }: { initialMonth?: string; onBack: () => void }) {
  const { runAuthenticated } = useAuth();
  const currentMonth = monthKey(new Date());
  const [month, setMonth] = useState(initialMonth || currentMonth);
  const [report, setReport] = useState<ProductSalesReportResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState('');

  const load = useCallback(async (refresh = false) => {
    if (refresh) setRefreshing(true);
    else setLoading(true);
    setError('');
    try {
      setReport(await runAuthenticated((token) => api.productSalesReport(token, month)));
    } catch (reason) {
      const message = reason instanceof Error ? reason.message : 'Report could not be loaded.';
      setError(message);
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }, [month, runAuthenticated]);

  useEffect(() => { void load(); }, [load]);

  const summary = report?.data.summary;

  return (
    <ScrollView
      contentContainerStyle={styles.content}
      refreshControl={<RefreshControl refreshing={refreshing} onRefresh={() => void load(true)} colors={['#0B5D3B']} tintColor="#0B5D3B" />}
    >
      <Pressable onPress={onBack} style={({ pressed }) => [styles.backButton, pressed && styles.pressed]}>
        <MaterialCommunityIcons color="#0B5D3B" name="arrow-left" size={21} />
        <Text style={styles.backText}>Back to home</Text>
      </Pressable>

      <View style={styles.monthRow}>
        <Pressable onPress={() => setMonth(moveMonth(month, -1))} style={styles.arrow}>
          <MaterialCommunityIcons name="chevron-left" size={25} color="#0B5D3B" />
        </Pressable>
        <View style={styles.monthCopy}>
          <Text style={styles.month}>{report?.meta.period.label || month}</Text>
          <Text style={styles.hint}>Product-wise monthly sales</Text>
        </View>
        <Pressable disabled={month === currentMonth} onPress={() => setMonth(moveMonth(month, 1))} style={[styles.arrow, month === currentMonth && styles.disabled]}>
          <MaterialCommunityIcons name="chevron-right" size={25} color="#0B5D3B" />
        </Pressable>
      </View>

      {error && report ? <View style={styles.warning}><Text style={styles.warningText}>{error} Showing the last loaded data.</Text></View> : null}
      {error && !report ? <View style={styles.warning}><Text style={styles.warningText}>{error}</Text></View> : null}

      <View style={styles.summaryGrid}>
        <View style={styles.summaryCard}>
          <Text style={styles.summaryLabel}>Products sold</Text>
          <Text style={styles.summaryValue}>{summary?.product_count ?? 0}</Text>
        </View>
        <View style={styles.summaryCard}>
          <Text style={styles.summaryLabel}>Total qty</Text>
          <Text style={styles.summaryValue}>{summary?.total_quantity ?? 0}</Text>
        </View>
        <View style={styles.summaryCardWide}>
          <Text style={styles.summaryLabel}>Total sales</Text>
          <Text style={styles.moneyValue}>{summary ? money(summary.total_sales) : '₹ 0.00'}</Text>
        </View>
        <View style={styles.summaryCardWide}>
          <Text style={styles.summaryLabel}>Total profit</Text>
          <Text style={styles.moneyValue}>{summary ? money(summary.total_profit) : '₹ 0.00'}</Text>
        </View>
      </View>

      {summary?.missing_cost_count ? (
        <View style={styles.costWarning}>
          <MaterialCommunityIcons color="#B77900" name="alert-circle-outline" size={22} />
          <Text style={styles.costWarningText}>{summary.missing_cost_count} product(s) need purchase price to calculate full profit.</Text>
        </View>
      ) : null}

      <Text style={styles.heading}>Product sales</Text>
      {loading && !report ? <ActivityIndicator color="#0B5D3B" /> : null}
      {!loading && !report?.data.products.length ? <Text style={styles.empty}>No product sales found for this month.</Text> : null}
      {report?.data.products.map((product) => (
        <View key={`${product.product_id || product.sku || product.name}`} style={styles.productCard}>
          <View style={styles.productTop}>
            <View style={styles.productIcon}>
              <MaterialCommunityIcons color="#14733D" name="package-variant-closed" size={23} />
            </View>
            <View style={styles.productCopy}>
              <Text numberOfLines={2} style={styles.productName}>{product.name}</Text>
              <Text style={styles.productMeta}>{product.sku || 'No SKU'} · Qty {product.quantity}</Text>
            </View>
          </View>
          <View style={styles.productTotals}>
            <View style={styles.productTotalBlock}>
              <Text style={styles.totalLabel}>Sale</Text>
              <Text style={styles.totalValue}>{money(product.total_sales)}</Text>
            </View>
            <View style={styles.productTotalBlock}>
              <Text style={styles.totalLabel}>Profit</Text>
              <Text style={[styles.totalValue, !product.profit_complete && styles.incompleteProfit]}>{money(product.total_profit)}</Text>
            </View>
          </View>
          {!product.profit_complete ? <Text style={styles.incompleteHint}>Purchase price missing</Text> : null}
        </View>
      ))}
    </ScrollView>
  );
}

const styles = StyleSheet.create({
  content: { padding: 16, paddingBottom: 35 },
  pressed: { opacity: 0.65 },
  backButton: { alignSelf: 'flex-start', minHeight: 42, borderRadius: 21, backgroundColor: '#EAF6EF', paddingHorizontal: 13, flexDirection: 'row', alignItems: 'center', columnGap: 6, marginBottom: 12 },
  backText: { color: '#0B5D3B', fontSize: 13, fontWeight: '900' },
  monthRow: { flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', backgroundColor: '#FFFFFF', padding: 12, borderRadius: 16, borderWidth: 1, borderColor: '#DFE7E3' },
  arrow: { width: 42, height: 42, borderRadius: 21, backgroundColor: '#EAF6EF', alignItems: 'center', justifyContent: 'center' },
  disabled: { opacity: 0.3 },
  monthCopy: { flex: 1, paddingHorizontal: 12 },
  month: { fontSize: 17, fontWeight: '900', color: '#17352A', textAlign: 'center' },
  hint: { fontSize: 11, color: '#71867D', textAlign: 'center', marginTop: 2 },
  warning: { backgroundColor: '#FFF4D8', borderColor: '#F0D08D', borderWidth: 1, borderRadius: 12, padding: 12, marginTop: 14 },
  warningText: { color: '#7A4A00', lineHeight: 19 },
  summaryGrid: { flexDirection: 'row', flexWrap: 'wrap', gap: 10, marginTop: 14 },
  summaryCard: { width: '48.5%', minHeight: 94, backgroundColor: '#FFFFFF', borderColor: '#DFE7E3', borderWidth: 1, borderRadius: 16, padding: 14, justifyContent: 'center' },
  summaryCardWide: { width: '48.5%', minHeight: 104, backgroundColor: '#F4FAF7', borderColor: '#BBDCC5', borderWidth: 1, borderRadius: 16, padding: 14, justifyContent: 'center' },
  summaryLabel: { color: '#587066', fontSize: 12, fontWeight: '800' },
  summaryValue: { color: '#08733F', fontSize: 28, fontWeight: '900', marginTop: 5 },
  moneyValue: { color: '#08733F', fontSize: 21, fontWeight: '900', marginTop: 7 },
  costWarning: { flexDirection: 'row', alignItems: 'center', columnGap: 9, backgroundColor: '#FFF8E7', borderColor: '#EAC06E', borderWidth: 1, borderRadius: 14, padding: 12, marginTop: 14 },
  costWarningText: { flex: 1, color: '#725018', fontSize: 12, lineHeight: 18, fontWeight: '700' },
  heading: { fontSize: 19, fontWeight: '900', color: '#17352A', marginVertical: 18 },
  empty: { color: '#71867D', textAlign: 'center', marginTop: 30 },
  productCard: { backgroundColor: '#FFFFFF', borderWidth: 1, borderColor: '#DFE7E3', borderRadius: 16, padding: 14, marginBottom: 10 },
  productTop: { flexDirection: 'row', alignItems: 'center' },
  productIcon: { width: 44, height: 44, borderRadius: 13, backgroundColor: '#EAF6EF', alignItems: 'center', justifyContent: 'center', marginRight: 11 },
  productCopy: { flex: 1 },
  productName: { color: '#17352A', fontSize: 15, fontWeight: '900' },
  productMeta: { color: '#71867D', fontSize: 12, marginTop: 4, fontWeight: '700' },
  productTotals: { flexDirection: 'row', borderTopColor: '#E7ECEA', borderTopWidth: 1, marginTop: 12, paddingTop: 12 },
  productTotalBlock: { flex: 1 },
  totalLabel: { color: '#71867D', fontSize: 11, fontWeight: '800', textTransform: 'uppercase' },
  totalValue: { color: '#0B5D3B', fontSize: 17, fontWeight: '900', marginTop: 4 },
  incompleteProfit: { color: '#B77900' },
  incompleteHint: { color: '#B77900', fontSize: 11, fontWeight: '800', marginTop: 8 },
});
