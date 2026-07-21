import { useState } from 'react';
import { ActivityIndicator, Pressable, StyleSheet, Text, TextInput, View } from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';

import { ApiError } from '../auth/api';
import { useAuth } from '../auth/AuthContext';
import OperationsApp from './OperationsApp';

export default function IndexScreen() {
  const { auth, loading, signIn, chooseTenant } = useAuth();
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');

  const run = async (action: () => Promise<void>) => {
    setBusy(true); setError('');
    try { await action(); } catch (reason) {
      setError(reason instanceof ApiError ? reason.message : 'Something went wrong. Try again.');
    } finally { setBusy(false); }
  };

  if (loading) return <SafeAreaView style={styles.center}><ActivityIndicator size="large" color="#0B5D3B" /></SafeAreaView>;

  if (!auth) return (
    <SafeAreaView style={styles.page}>
      <View style={styles.brand}><Text style={styles.brandText}>M</Text></View>
      <Text style={styles.title}>Mathukai Operations</Text>
      <Text style={styles.subtitle}>Sign in to manage today’s operations.</Text>
      <View style={styles.form}>
        <Text style={styles.label}>Username</Text>
        <TextInput autoCapitalize="none" autoCorrect={false} editable={!busy} value={username} onChangeText={setUsername} style={styles.input} accessibilityLabel="Username" />
        <Text style={styles.label}>Password</Text>
        <TextInput editable={!busy} secureTextEntry value={password} onChangeText={setPassword} style={styles.input} accessibilityLabel="Password" onSubmitEditing={() => void run(() => signIn(username, password))} />
        {error ? <Text style={styles.error}>{error}</Text> : null}
        <Pressable disabled={busy || !username.trim() || !password} onPress={() => void run(() => signIn(username, password))} style={({ pressed }) => [styles.button, (pressed || busy) && styles.buttonMuted]}>
          {busy ? <ActivityIndicator color="#FFF" /> : <Text style={styles.buttonText}>Sign in</Text>}
        </Pressable>
      </View>
    </SafeAreaView>
  );

  if (!auth.session.active_tenant) return (
    <SafeAreaView style={styles.page}>
      <Text style={styles.title}>Choose workspace</Text>
      <Text style={styles.subtitle}>Select the business you want to manage.</Text>
      <View style={styles.form}>
        {auth.session.available_tenants.map((tenant) => (
          <Pressable key={tenant.tenant_id} disabled={busy} onPress={() => void run(() => chooseTenant(tenant.tenant_id))} style={styles.tenant}>
            <Text style={styles.tenantName}>{tenant.tenant_name}</Text><Text style={styles.tenantRole}>{tenant.role_label}</Text>
          </Pressable>
        ))}
        {error ? <Text style={styles.error}>{error}</Text> : null}
      </View>
    </SafeAreaView>
  );

  return <OperationsApp />;
}

const styles = StyleSheet.create({
  center: { flex: 1, alignItems: 'center', justifyContent: 'center', backgroundColor: '#F4F7F5' },
  page: { flex: 1, justifyContent: 'center', backgroundColor: '#F4F7F5', paddingHorizontal: 28 },
  brand: { width: 72, height: 72, borderRadius: 20, backgroundColor: '#0B5D3B', alignItems: 'center', justifyContent: 'center', alignSelf: 'center', marginBottom: 22 },
  brandText: { color: '#FFF', fontSize: 36, fontWeight: '800' },
  title: { color: '#17352A', fontSize: 30, fontWeight: '800', textAlign: 'center' },
  subtitle: { color: '#587066', fontSize: 16, lineHeight: 23, textAlign: 'center', marginTop: 9 },
  form: { marginTop: 34, gap: 10 }, label: { color: '#29483D', fontSize: 14, fontWeight: '700', marginTop: 5 },
  input: { backgroundColor: '#FFF', borderColor: '#C8D6D0', borderWidth: 1, borderRadius: 13, minHeight: 54, paddingHorizontal: 16, color: '#17352A', fontSize: 17 },
  error: { color: '#B42318', lineHeight: 20, marginTop: 4 },
  button: { backgroundColor: '#0B5D3B', borderRadius: 14, minHeight: 56, alignItems: 'center', justifyContent: 'center', marginTop: 12 },
  buttonMuted: { opacity: 0.6 }, buttonText: { color: '#FFF', fontSize: 17, fontWeight: '800' },
  tenant: { backgroundColor: '#FFF', borderColor: '#D5E0DB', borderWidth: 1, borderRadius: 15, padding: 18 },
  tenantName: { color: '#17352A', fontSize: 17, fontWeight: '800' }, tenantRole: { color: '#587066', marginTop: 4 },
});
