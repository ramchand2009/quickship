import { useCallback, useEffect, useState } from 'react';
import MaterialCommunityIcons from '@expo/vector-icons/MaterialCommunityIcons';
import { ActivityIndicator, Alert, KeyboardAvoidingView, Modal, Platform, Pressable, ScrollView, StyleSheet, Text, TextInput, View } from 'react-native';
import * as api from '../auth/api';
import { useAuth } from '../auth/AuthContext';
import type { Expense, ExpenseListResponse } from './types';

const monthKey = (date: Date) => `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, '0')}`;
function moveMonth(value: string, amount: number) { const [year, month] = value.split('-').map(Number); return monthKey(new Date(year, month - 1 + amount, 1)); }
const requestKey = () => `${Date.now()}-${Math.random().toString(16).slice(2)}`;

export default function ExpensesScreen() {
  const { runAuthenticated } = useAuth();
  const current = monthKey(new Date());
  const [month, setMonth] = useState(current);
  const [result, setResult] = useState<ExpenseListResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [formVisible, setFormVisible] = useState(false);
  const [saving, setSaving] = useState(false);
  const [editingExpense, setEditingExpense] = useState<Expense | null>(null);
  const [item, setItem] = useState('');
  const [quantity, setQuantity] = useState('1');
  const [price, setPrice] = useState('');
  const [remark, setRemark] = useState('');

  const load = useCallback(async () => {
    setLoading(true);
    try { setResult(await runAuthenticated((token) => api.expenses(token, month))); }
    catch (error) { Alert.alert('Expenses unavailable', error instanceof Error ? error.message : 'Please try again.'); }
    finally { setLoading(false); }
  }, [month, runAuthenticated]);
  useEffect(() => { void load(); }, [load]);

  function openAdd() { setEditingExpense(null); setItem(''); setQuantity('1'); setPrice(''); setRemark(''); setFormVisible(true); }
  function openEdit(expense: Expense) { setEditingExpense(expense); setItem(expense.item_name); setQuantity(String(expense.quantity)); setPrice(expense.unit_price); setRemark(expense.remark || ''); setFormVisible(true); }

  async function save() {
    const parsedQuantity = Number(quantity); const parsedPrice = Number(price);
    if (!item.trim() || !Number.isInteger(parsedQuantity) || parsedQuantity < 1 || !Number.isFinite(parsedPrice) || parsedPrice < 0) { Alert.alert('Check expense', 'Enter a valid item, quantity and unit price.'); return; }
    const values = { item_name: item.trim(), quantity: parsedQuantity, unit_price: parsedPrice.toFixed(2), remark: remark.trim() };
    setSaving(true);
    try {
      if (editingExpense) await runAuthenticated((token) => api.updateExpense(token, editingExpense.id, values, requestKey()));
      else await runAuthenticated((token) => api.createExpense(token, values, requestKey()));
      setFormVisible(false);
      if (!editingExpense && month !== current) setMonth(current); else await load();
    } catch (error) { Alert.alert('Expense not saved', error instanceof Error ? error.message : 'Please try again.'); }
    finally { setSaving(false); }
  }

  function confirmDelete(expense: Expense) {
    Alert.alert('Delete expense?', `${expense.item_name} will be permanently removed.`, [
      { text: 'Cancel', style: 'cancel' },
      { text: 'Delete', style: 'destructive', onPress: async () => {
        try { await runAuthenticated((token) => api.deleteExpense(token, expense.id, requestKey())); await load(); }
        catch (error) { Alert.alert('Expense not deleted', error instanceof Error ? error.message : 'Please try again.'); }
      } },
    ]);
  }

  return <>
    <ScrollView contentContainerStyle={styles.content}>
      <View style={styles.monthRow}><Pressable onPress={() => setMonth(moveMonth(month, -1))} style={styles.arrow}><MaterialCommunityIcons name="chevron-left" size={25} color="#0B5D3B" /></Pressable><View><Text style={styles.month}>{result?.meta.period.label || month}</Text><Text style={styles.hint}>Monthly expenses</Text></View><Pressable disabled={month === current} onPress={() => setMonth(moveMonth(month, 1))} style={[styles.arrow, month === current && styles.disabled]}><MaterialCommunityIcons name="chevron-right" size={25} color="#0B5D3B" /></Pressable></View>
      <View style={styles.totalCard}><Text style={styles.totalLabel}>Total expenses</Text><Text style={styles.total}>₹ {result?.data.total || '0.00'}</Text></View>
      <Pressable onPress={openAdd} style={styles.add}><MaterialCommunityIcons name="plus" size={22} color="#FFFFFF" /><Text style={styles.addText}>Add expense</Text></Pressable>
      <Text style={styles.heading}>Expense history</Text>
      {loading ? <ActivityIndicator color="#0B5D3B" /> : result?.data.expenses.length ? result.data.expenses.map((expense) => <View key={expense.id} style={styles.card}><View style={styles.cardTop}><Text style={styles.item}>{expense.item_name}</Text><Text style={styles.amount}>₹ {expense.total_amount}</Text></View><Text style={styles.meta}>{expense.quantity} × ₹ {expense.unit_price} · {new Date(expense.created_at).toLocaleDateString()}</Text>{expense.remark ? <Text style={styles.remark}>{expense.remark}</Text> : null}<View style={styles.cardActions}><Pressable onPress={() => openEdit(expense)} style={styles.editAction}><MaterialCommunityIcons name="pencil-outline" size={18} color="#0B5D3B" /><Text style={styles.editText}>Edit</Text></Pressable><Pressable onPress={() => confirmDelete(expense)} style={styles.deleteAction}><MaterialCommunityIcons name="trash-can-outline" size={18} color="#B42318" /><Text style={styles.deleteText}>Delete</Text></Pressable></View></View>) : <Text style={styles.empty}>No expenses recorded for this month.</Text>}
    </ScrollView>
    <Modal transparent animationType="slide" visible={formVisible} onRequestClose={() => !saving && setFormVisible(false)}><KeyboardAvoidingView behavior={Platform.OS === 'ios' ? 'padding' : 'height'} style={styles.backdrop}><View style={styles.sheet}><ScrollView keyboardShouldPersistTaps="handled"><View style={styles.sheetHeader}><Text style={styles.sheetTitle}>{editingExpense ? 'Edit expense' : 'Add expense'}</Text><Pressable disabled={saving} onPress={() => setFormVisible(false)}><MaterialCommunityIcons name="close" size={25} color="#40564D" /></Pressable></View><Text style={styles.label}>Item or purpose</Text><TextInput value={item} onChangeText={setItem} style={styles.input} placeholder="Packaging material" /><View style={styles.formRow}><View style={styles.half}><Text style={styles.label}>Quantity</Text><TextInput value={quantity} onChangeText={setQuantity} keyboardType="number-pad" style={styles.input} /></View><View style={styles.half}><Text style={styles.label}>Unit price</Text><TextInput value={price} onChangeText={setPrice} keyboardType="decimal-pad" style={styles.input} placeholder="0.00" /></View></View><Text style={styles.label}>Note (optional)</Text><TextInput value={remark} onChangeText={setRemark} style={[styles.input, styles.note]} multiline /><Pressable disabled={saving} onPress={() => void save()} style={styles.save}>{saving ? <ActivityIndicator color="#FFFFFF" /> : <Text style={styles.saveText}>{editingExpense ? 'Update expense' : 'Save expense'}</Text>}</Pressable></ScrollView></View></KeyboardAvoidingView></Modal>
  </>;
}

const styles = StyleSheet.create({content:{padding:16,paddingBottom:35},monthRow:{flexDirection:'row',alignItems:'center',justifyContent:'space-between',backgroundColor:'#FFF',padding:12,borderRadius:16,borderWidth:1,borderColor:'#DFE7E3'},arrow:{width:42,height:42,borderRadius:21,backgroundColor:'#EAF6EF',alignItems:'center',justifyContent:'center'},disabled:{opacity:.3},month:{fontSize:17,fontWeight:'900',color:'#17352A',textAlign:'center'},hint:{fontSize:11,color:'#71867D',textAlign:'center'},totalCard:{backgroundColor:'#0B5D3B',borderRadius:18,padding:20,marginTop:14},totalLabel:{color:'#BCE3CC',fontWeight:'700'},total:{color:'#FFF',fontSize:29,fontWeight:'900',marginTop:6},add:{height:52,borderRadius:14,backgroundColor:'#14733D',flexDirection:'row',alignItems:'center',justifyContent:'center',gap:7,marginTop:14},addText:{color:'#FFF',fontWeight:'900',fontSize:16},heading:{fontSize:19,fontWeight:'900',color:'#17352A',marginVertical:18},card:{backgroundColor:'#FFF',borderWidth:1,borderColor:'#DFE7E3',borderRadius:15,padding:14,marginBottom:10},cardTop:{flexDirection:'row',justifyContent:'space-between',gap:10},item:{flex:1,color:'#29483D',fontWeight:'900',fontSize:15},amount:{color:'#0B5D3B',fontWeight:'900',fontSize:16},meta:{color:'#71867D',fontSize:12,marginTop:6},remark:{color:'#40564D',fontSize:13,marginTop:7},cardActions:{flexDirection:'row',gap:9,marginTop:12,paddingTop:10,borderTopWidth:1,borderTopColor:'#E7ECEA'},editAction:{flex:1,minHeight:40,borderRadius:10,backgroundColor:'#EAF6EF',flexDirection:'row',alignItems:'center',justifyContent:'center',gap:5},editText:{color:'#0B5D3B',fontWeight:'800'},deleteAction:{flex:1,minHeight:40,borderRadius:10,backgroundColor:'#FFF1F0',flexDirection:'row',alignItems:'center',justifyContent:'center',gap:5},deleteText:{color:'#B42318',fontWeight:'800'},empty:{color:'#71867D',textAlign:'center',marginTop:30},backdrop:{flex:1,justifyContent:'flex-end',backgroundColor:'rgba(15,35,28,.5)'},sheet:{maxHeight:'88%',backgroundColor:'#FFF',borderTopLeftRadius:22,borderTopRightRadius:22,padding:18,paddingBottom:28},sheetHeader:{flexDirection:'row',justifyContent:'space-between',alignItems:'center',marginBottom:16},sheetTitle:{fontSize:21,fontWeight:'900',color:'#17352A'},label:{fontSize:13,fontWeight:'800',color:'#40564D',marginBottom:6},input:{height:50,borderWidth:1,borderColor:'#CAD7D1',borderRadius:12,paddingHorizontal:13,fontSize:15,color:'#17352A',marginBottom:14},note:{height:78,paddingTop:12,textAlignVertical:'top'},formRow:{flexDirection:'row',gap:10},half:{flex:1},save:{height:52,borderRadius:14,backgroundColor:'#0B5D3B',alignItems:'center',justifyContent:'center'},saveText:{color:'#FFF',fontWeight:'900',fontSize:16}});
