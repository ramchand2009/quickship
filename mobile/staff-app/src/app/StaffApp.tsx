import { useState } from 'react';
import MaterialCommunityIcons from '@expo/vector-icons/MaterialCommunityIcons';
import { Image, Pressable, StyleSheet, Text, View } from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';

import { useAuth } from '../auth/AuthContext';
import OrdersScreen from '../orders/OrdersScreen';
import StockScreen from '../stock/StockScreen';

type StaffTab = 'orders' | 'stock' | 'account';

const TABS: Array<{ key: StaffTab; label: string; icon: keyof typeof MaterialCommunityIcons.glyphMap }> = [
  { key: 'orders', label: 'Orders', icon: 'clipboard-list-outline' },
  { key: 'stock', label: 'Stock', icon: 'cube-outline' },
  { key: 'account', label: 'Account', icon: 'account-circle-outline' },
];

function tabTitle(tab: StaffTab) {
  if (tab === 'orders') return 'Staff Orders';
  if (tab === 'stock') return 'Stock Lookup';
  return 'Account';
}

function AccountScreen() {
  const { auth, signOut } = useAuth();
  const user = auth?.session.user;
  const tenant = auth?.session.active_tenant;

  return (
    <View style={styles.accountPage}>
      <View style={styles.accountCard}>
        <View style={styles.avatar}>
          <Text style={styles.avatarText}>{(user?.display_name || user?.username || 'S').slice(0, 1).toUpperCase()}</Text>
        </View>
        <Text style={styles.accountName}>{user?.display_name || user?.username || 'Staff user'}</Text>
        <Text style={styles.accountMeta}>{tenant?.tenant_name || 'Mathukai Organic'}</Text>
        <Text style={styles.accountRole}>{tenant?.role_label || 'Staff access'}</Text>
      </View>

      <View style={styles.infoCard}>
        <Text style={styles.infoTitle}>Staff app access</Text>
        <Text style={styles.infoText}>This app is focused on daily order processing and stock checks. Owner-only reports, expenses, and profit dashboards are hidden here.</Text>
      </View>

      <Pressable onPress={() => void signOut()} style={({ pressed }) => [styles.signOutButton, pressed && styles.pressed]}>
        <MaterialCommunityIcons name="logout" color="#B42318" size={20} />
        <Text style={styles.signOutText}>Sign out</Text>
      </Pressable>
    </View>
  );
}

export default function StaffApp() {
  const [activeTab, setActiveTab] = useState<StaffTab>('orders');

  return (
    <SafeAreaView style={styles.shell}>
      <View style={styles.header}>
        <View style={styles.brandRow}>
          <Image
            accessibilityLabel="Mathukai Organic logo"
            resizeMode="contain"
            source={require('../../assets/images/mathukai-organic-logo-transparent.png')}
            style={styles.logo}
          />
          <View>
            <Text style={styles.eyebrow}>MATHUKAI STAFF</Text>
            <Text style={styles.title}>{tabTitle(activeTab)}</Text>
          </View>
        </View>
      </View>

      <View style={styles.content}>
        {activeTab === 'orders' ? <OrdersScreen initialFilters={{ status: 'new_order' }} /> : null}
        {activeTab === 'stock' ? <StockScreen /> : null}
        {activeTab === 'account' ? <AccountScreen /> : null}
      </View>

      <View style={styles.bottomTabs}>
        {TABS.map((tab) => {
          const active = tab.key === activeTab;
          return (
            <Pressable key={tab.key} onPress={() => setActiveTab(tab.key)} style={styles.tabButton}>
              <MaterialCommunityIcons name={tab.icon} size={24} color={active ? '#006B44' : '#70867D'} />
              <Text style={[styles.tabLabel, active && styles.tabLabelActive]}>{tab.label}</Text>
            </Pressable>
          );
        })}
      </View>
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  shell: { flex: 1, backgroundColor: '#F7FAF8' },
  header: { borderBottomWidth: 1, borderBottomColor: '#DCE7E1', backgroundColor: '#FFFFFF', paddingHorizontal: 16, paddingVertical: 12 },
  brandRow: { flexDirection: 'row', alignItems: 'center', gap: 12 },
  logo: { width: 42, height: 42 },
  eyebrow: { color: '#6B8177', fontSize: 10, fontWeight: '800', letterSpacing: 1.2 },
  title: { color: '#17352A', fontSize: 20, fontWeight: '900', marginTop: 2 },
  content: { flex: 1 },
  bottomTabs: { flexDirection: 'row', borderTopWidth: 1, borderTopColor: '#DCE7E1', backgroundColor: '#FFFFFF', paddingTop: 8, paddingBottom: 10 },
  tabButton: { flex: 1, alignItems: 'center', justifyContent: 'center', gap: 3 },
  tabLabel: { color: '#70867D', fontSize: 12, fontWeight: '700' },
  tabLabelActive: { color: '#006B44' },
  accountPage: { flex: 1, padding: 16, gap: 14 },
  accountCard: { alignItems: 'center', backgroundColor: '#FFFFFF', borderColor: '#DCE7E1', borderWidth: 1, borderRadius: 22, padding: 24 },
  avatar: { width: 70, height: 70, borderRadius: 35, alignItems: 'center', justifyContent: 'center', backgroundColor: '#E4F5EC', marginBottom: 12 },
  avatarText: { color: '#006B44', fontSize: 30, fontWeight: '900' },
  accountName: { color: '#17352A', fontSize: 22, fontWeight: '900' },
  accountMeta: { color: '#587066', marginTop: 6, fontSize: 15 },
  accountRole: { color: '#006B44', marginTop: 10, fontWeight: '800' },
  infoCard: { backgroundColor: '#FFFDF5', borderColor: '#F5D48B', borderWidth: 1, borderRadius: 18, padding: 16 },
  infoTitle: { color: '#3C2D0A', fontSize: 16, fontWeight: '900', marginBottom: 6 },
  infoText: { color: '#6B5B32', lineHeight: 21 },
  signOutButton: { minHeight: 52, borderRadius: 16, borderWidth: 1, borderColor: '#F3B8B4', backgroundColor: '#FFF5F4', flexDirection: 'row', alignItems: 'center', justifyContent: 'center', gap: 8 },
  signOutText: { color: '#B42318', fontSize: 16, fontWeight: '900' },
  pressed: { opacity: 0.65 },
});
