import React, { useMemo, useState } from 'react';
import { View, Text, StyleSheet, Pressable, FlatList, Alert } from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';

import { api, SubscriptionPlan } from '../api/client';

type Props = {
  token: string;
  navigation: any;
};

type PlanCard = {
  key: SubscriptionPlan;
  title: string;
  billing_period: string;
  price_label: string;
  original_price_label: string;
  discount_label: string;
};

const PREMIUM_BENEFITS = [
  'Unlimited AI auto-cut exports',
  'Priority render queue',
  'Advanced subtitles and silence cleanup',
  'Cloud project history and restore',
  'Creator support with faster turnaround',
  'Early access to new editing models',
];

const DEFAULT_PLANS: PlanCard[] = [
  {
    key: 'pro_monthly',
    title: 'Pro Monthly',
    billing_period: '1 month',
    price_label: '$19.99',
    original_price_label: '',
    discount_label: '',
  },
  {
    key: 'pro_yearly',
    title: 'Pro Yearly',
    billing_period: '1 year',
    price_label: '$99.00',
    original_price_label: '$239.88',
    discount_label: '-59%',
  },
  {
    key: 'free',
    title: 'Continue Free',
    billing_period: 'forever',
    price_label: '$0',
    original_price_label: '',
    discount_label: '',
  },
];

export default function PricingScreen({ token, navigation }: Props) {
  const [plans] = useState(DEFAULT_PLANS);
  const [selectedPlanKey, setSelectedPlanKey] = useState<SubscriptionPlan>('pro_yearly');
  const [busy, setBusy] = useState(false);

  const activePlan = useMemo(() => plans.find((plan) => plan.key === selectedPlanKey) || plans[0], [plans, selectedPlanKey]);

  const subscribe = async () => {
    if (!activePlan || busy) return;

    try {
      setBusy(true);
      if (activePlan.key !== 'free') {
        await api.startPayment(token, activePlan.key);
        await api.activatePayment(token, activePlan.key);
      }
      Alert.alert('Success', activePlan.key === 'free' ? 'Free plan is active.' : 'AutoCutAI Pro activated.');
      navigation.goBack();
    } catch (error: any) {
      Alert.alert('Unable to subscribe', String(error?.message || error || 'Try again.'));
    } finally {
      setBusy(false);
    }
  };

  return (
    <SafeAreaView style={styles.container} edges={['top']}>
      <Text style={styles.title}>AutoCutAI Pricing</Text>
      <Text style={styles.subtitle}>Choose a plan for your editing workflow.</Text>

      <FlatList
        data={plans}
        keyExtractor={(item) => item.key}
        showsVerticalScrollIndicator={false}
        renderItem={({ item }) => (
          <Pressable
            style={[styles.planCard, item.key === activePlan?.key && styles.planCardActive]}
            onPress={() => setSelectedPlanKey(item.key)}
          >
            <View style={styles.planRow}>
              <Text style={styles.planTitle}>{item.title}</Text>
              {item.discount_label ? (
                <View style={styles.discountBadge}>
                  <Text style={styles.discountText}>{item.discount_label}</Text>
                </View>
              ) : null}
            </View>
            <View style={styles.planPriceRow}>
              {item.original_price_label ? <Text style={styles.planOldPrice}>{item.original_price_label}</Text> : null}
              <Text style={styles.planPrice}>{item.price_label}</Text>
              <Text style={styles.planPeriod}>/ {item.billing_period}</Text>
            </View>
            {PREMIUM_BENEFITS.map((feature) => (
              <Text key={feature} style={styles.planFeature}>• {feature}</Text>
            ))}
          </Pressable>
        )}
      />

      <Pressable style={[styles.button, busy && styles.disabled]} disabled={busy} onPress={subscribe}>
        <Text style={styles.buttonText}>{busy ? 'Activating...' : 'Subscribe'}</Text>
      </Pressable>
      <Pressable onPress={() => navigation.goBack()} style={styles.linkWrap}>
        <Text style={styles.linkText}>Cancel</Text>
      </Pressable>
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: '#f5f9ff', padding: 20, paddingTop: 28 },
  title: { fontSize: 32, fontWeight: '700', color: '#031b33' },
  subtitle: { marginTop: 8, color: '#4c6077', fontSize: 15, marginBottom: 8 },
  planCard: { marginTop: 12, backgroundColor: '#fff', borderRadius: 12, borderWidth: 1, borderColor: '#cfdded', padding: 12 },
  planCardActive: { borderColor: '#031b33', borderWidth: 2 },
  planRow: { flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center' },
  planTitle: { fontSize: 17, fontWeight: '700', color: '#0b2845' },
  discountBadge: { backgroundColor: '#031b33', borderRadius: 999, paddingHorizontal: 8, paddingVertical: 4 },
  discountText: { color: '#fff', fontWeight: '700', fontSize: 11 },
  planPriceRow: { marginTop: 6, flexDirection: 'row', alignItems: 'baseline', gap: 6 },
  planOldPrice: { color: '#778ba1', textDecorationLine: 'line-through', fontWeight: '600' },
  planPrice: { color: '#0b2845', fontWeight: '700', fontSize: 20 },
  planPeriod: { color: '#526b83', fontWeight: '600' },
  planFeature: { marginTop: 4, color: '#4c6077' },
  button: { marginTop: 16, backgroundColor: '#031b33', borderRadius: 12, padding: 14, alignItems: 'center' },
  buttonText: { color: '#fff', fontWeight: '700' },
  linkWrap: { marginTop: 12, alignItems: 'center' },
  linkText: { color: '#1f4467', fontWeight: '600' },
  disabled: { opacity: 0.6 },
});
