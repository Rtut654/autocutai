import React from 'react';
import { View, Text, StyleSheet, Pressable } from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';

import { MeUser } from '../api/client';

type Props = {
  user: MeUser;
  onLogout: () => Promise<void>;
  onOpenPricing: () => void;
};

function planLabel(plan: string) {
  if (plan === 'pro_yearly') return 'Pro Yearly';
  if (plan === 'pro_monthly') return 'Pro Monthly';
  return 'Free';
}

export default function ProfileScreen({ user, onLogout, onOpenPricing }: Props) {
  const isFree = user.subscription_plan === 'free';

  return (
    <SafeAreaView style={styles.container} edges={['top']}>
      <Text style={styles.title}>Profile</Text>
      <Text style={styles.subtitle}>Manage account and billing.</Text>

      <View style={styles.card}>
        <Text style={styles.label}>Email</Text>
        <Text style={styles.value}>{user.email}</Text>

        <Text style={styles.label}>Plan</Text>
        <Text style={styles.value}>{planLabel(user.subscription_plan)}</Text>

        <Text style={styles.label}>Onboarding</Text>
        <Text style={styles.value}>{user.onboarding_completed ? 'Completed' : 'Pending'}</Text>

        <Pressable style={styles.button} onPress={onOpenPricing}>
          <Text style={styles.buttonText}>{isFree ? 'Upgrade to Pro' : 'Manage Plan'}</Text>
        </Pressable>

        <Pressable style={styles.secondaryButton} onPress={onLogout}>
          <Text style={styles.secondaryButtonText}>Log Out</Text>
        </Pressable>
      </View>
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: '#f5f9ff', padding: 20, gap: 8 },
  title: { fontSize: 32, fontWeight: '700', color: '#031b33' },
  subtitle: { color: '#4c6077', marginBottom: 8 },
  card: { backgroundColor: '#fff', borderRadius: 14, borderWidth: 1, borderColor: '#cfdded', padding: 14, gap: 8 },
  label: { fontSize: 12, color: '#70869d', textTransform: 'uppercase', letterSpacing: 0.4 },
  value: { fontSize: 16, color: '#0b2845', fontWeight: '600', marginBottom: 4 },
  button: { marginTop: 8, backgroundColor: '#031b33', borderRadius: 10, padding: 12, alignItems: 'center' },
  buttonText: { color: '#fff', fontWeight: '700' },
  secondaryButton: { marginTop: 8, backgroundColor: '#dce9f7', borderRadius: 10, padding: 12, alignItems: 'center' },
  secondaryButtonText: { color: '#0d2d4b', fontWeight: '700' },
});
