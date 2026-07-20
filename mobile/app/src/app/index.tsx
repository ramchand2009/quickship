import { StyleSheet, Text, View } from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';

export default function HomeScreen() {
  return (
    <SafeAreaView style={styles.safeArea}>
      <View style={styles.mark} accessibilityElementsHidden>
        <Text style={styles.markText}>M</Text>
      </View>
      <Text accessibilityRole="header" style={styles.title}>
        Mathukai Operations
      </Text>
      <Text style={styles.subtitle}>Android application foundation is ready.</Text>
      <View style={styles.statusCard}>
        <View style={styles.statusDot} />
        <Text style={styles.statusText}>Development build configured</Text>
      </View>
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  safeArea: {
    flex: 1,
    backgroundColor: '#F4F7F5',
    paddingHorizontal: 28,
    justifyContent: 'center',
    alignItems: 'center',
    gap: 16,
  },
  mark: {
    width: 88,
    height: 88,
    borderRadius: 24,
    backgroundColor: '#0B5D3B',
    alignItems: 'center',
    justifyContent: 'center',
    marginBottom: 8,
  },
  markText: {
    color: '#FFFFFF',
    fontSize: 44,
    fontWeight: '800',
  },
  title: {
    color: '#17352A',
    fontSize: 30,
    fontWeight: '700',
    textAlign: 'center',
  },
  subtitle: {
    color: '#587066',
    fontSize: 16,
    lineHeight: 24,
    textAlign: 'center',
  },
  statusCard: {
    alignItems: 'center',
    backgroundColor: '#E2F1E9',
    borderRadius: 16,
    flexDirection: 'row',
    gap: 10,
    marginTop: 16,
    paddingHorizontal: 18,
    paddingVertical: 14,
  },
  statusDot: {
    width: 10,
    height: 10,
    borderRadius: 5,
    backgroundColor: '#15824F',
  },
  statusText: {
    color: '#174E36',
    fontSize: 14,
    fontWeight: '600',
  },
});
