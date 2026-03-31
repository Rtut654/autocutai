import React, { useEffect, useMemo, useRef, useState } from 'react';
import { View, Text, StyleSheet, Pressable, FlatList, Alert, Linking, Platform } from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';
import { MaterialCommunityIcons } from '@expo/vector-icons';
import * as InAppPurchases from 'expo-in-app-purchases';

import { api } from '../api/client';

const TERMS_OF_USE_URL = 'https://autocutai.app/terms-of-use';
const PRIVACY_POLICY_URL = 'https://autocutai.app/privacy-policy';
const APPLE_SUBSCRIPTIONS_URL = 'https://apps.apple.com/account/subscriptions';
const PLAY_SUBSCRIPTIONS_URL = 'https://play.google.com/store/account/subscriptions';

const PREMIUM_BENEFITS = [
  'Unlimited AI video projects',
  'Advanced subtitle and silence cleanup',
  'Priority processing queue',
  'Cloud project history and restore',
  'Faster export and collaboration features',
  'Early access to new AI editing tools',
];

const PLAN_TO_PRODUCT_ID: Record<string, string> = {
  monthly: '1m_sub_autocutai',
  six_month: '6m_sub_autocutai',
  yearly: '1y_sub_autocutai',
};

const DEFAULT_PLANS = [
  {
    key: 'monthly',
    title: '1 Month',
    billing_period: '1 month',
    price_label: '$19.99',
    original_price_label: '',
    discount_label: '',
  },
  {
    key: 'six_month',
    title: '6 Months',
    billing_period: '6 months',
    price_label: '$59.99',
    original_price_label: '$99.99',
    discount_label: '-40%',
  },
  {
    key: 'yearly',
    title: '1 Year',
    billing_period: '1 year',
    price_label: '$99.00',
    original_price_label: '$247.50',
    discount_label: '-60%',
  },
];

const PRODUCT_ID_TO_PLAN = Object.fromEntries(
  Object.entries(PLAN_TO_PRODUCT_ID).map(([planKey, productId]) => [productId, planKey]),
);

function isAlreadyConnectedStoreError(error: any) {
  const message = String(error?.message || error || '').toLowerCase();
  return message.includes('already connected');
}

function mapStoreProducts(results: any[]) {
  const mapped: Record<string, any> = {};
  for (const product of Array.isArray(results) ? results : []) {
    const productId = String(product?.productId || product?.productIdentifier || '');
    if (!productId) continue;
    mapped[productId] = product;
  }
  return mapped;
}

function isAlreadyOwnedOrSubscribedError(error: any) {
  const normalized = String(error?.message || error || '').toLowerCase();
  return normalized.includes('already')
    && (normalized.includes('owned') || normalized.includes('subscribed') || normalized.includes('purchased'));
}

function getProductPriceLabel(product: any) {
  return String(
    product?.price
    || product?.localizedPrice
    || product?.priceString
    || '',
  ).trim();
}

function parseDiscountRatio(label: string) {
  const match = String(label || '').match(/(-?\d+(?:\.\d+)?)\s*%/);
  if (!match) return 0;
  const pct = Math.abs(Number(match[1])) / 100;
  if (!Number.isFinite(pct) || pct <= 0 || pct >= 0.95) return 0;
  return pct;
}

function parseNumericPrice(rawValue: string | number) {
  if (Number.isFinite(Number(rawValue))) return Number(rawValue);
  const cleaned = String(rawValue || '').replace(/[^0-9,.-]+/g, '');
  if (!cleaned) return NaN;
  const commaIdx = cleaned.lastIndexOf(',');
  const dotIdx = cleaned.lastIndexOf('.');
  let normalized = cleaned;
  if (commaIdx >= 0 && dotIdx >= 0) {
    if (commaIdx > dotIdx) {
      normalized = cleaned.replace(/\./g, '').replace(',', '.');
    } else {
      normalized = cleaned.replace(/,/g, '');
    }
  } else if (commaIdx >= 0) {
    normalized = cleaned.replace(',', '.');
  }
  return Number(normalized);
}

function getStoreAmountAndCurrency(product: any) {
  const amountMicros = Number(product?.priceAmountMicros);
  if (Number.isFinite(amountMicros) && amountMicros > 0) {
    return {
      amount: amountMicros / 1_000_000,
      currencyCode: String(product?.currencyCode || product?.priceCurrencyCode || '').trim() || null,
    };
  }
  const amount = parseNumericPrice(product?.price);
  if (Number.isFinite(amount) && amount > 0) {
    return {
      amount,
      currencyCode: String(product?.currencyCode || product?.priceCurrencyCode || '').trim() || null,
    };
  }
  return { amount: NaN, currencyCode: null };
}

function formatCurrency(amount: number, currencyCode: string | null) {
  if (!Number.isFinite(amount)) return '';
  if (!currencyCode) return amount.toFixed(2);
  try {
    return new Intl.NumberFormat(undefined, {
      style: 'currency',
      currency: currencyCode,
      minimumFractionDigits: 2,
      maximumFractionDigits: 2,
    }).format(amount);
  } catch {
    return `${currencyCode} ${amount.toFixed(2)}`;
  }
}

function resolveDisplayedPrices(plan: any, storeProduct: any) {
  const fallbackCurrent = getProductPriceLabel(storeProduct) || plan.price_label;
  const fallbackOld = String(plan.original_price_label || '').trim();
  const discountRatio = parseDiscountRatio(plan.discount_label);
  const { amount, currencyCode } = getStoreAmountAndCurrency(storeProduct);
  if (!Number.isFinite(amount) || amount <= 0) {
    return {
      currentLabel: fallbackCurrent,
      oldLabel: discountRatio > 0 ? fallbackOld : '',
    };
  }
  const currentLabel = formatCurrency(amount, currencyCode) || fallbackCurrent;
  if (discountRatio <= 0) {
    return { currentLabel, oldLabel: '' };
  }
  const originalAmount = amount / (1 - discountRatio);
  const oldLabel = formatCurrency(originalAmount, currencyCode) || fallbackOld;
  return { currentLabel, oldLabel };
}

function getPlanBenefits() {
  return PREMIUM_BENEFITS;
}

function readPurchaseField(purchase: any, keys: string[]) {
  for (const key of keys) {
    const value = purchase?.[key];
    if (typeof value === 'string' && value.trim()) return value.trim();
  }
  return '';
}

type Props = {
  token: string;
  navigation: any;
  onRequireAccount?: () => void;
};

export default function PricingScreen({ token, navigation, onRequireAccount }: Props) {
  const [plans, setPlans] = useState<any[]>([]);
  const [selectedPlanKey, setSelectedPlanKey] = useState('six_month');
  const [busy, setBusy] = useState(false);
  const [storeProductsById, setStoreProductsById] = useState<Record<string, any>>({});
  const pendingPlanKeyRef = useRef<string | null>(null);
  const purchaseHandledRef = useRef(false);
  const tokenRef = useRef(token);
  const onRequireAccountRef = useRef(onRequireAccount);
  const storeSubscriptionUrl = Platform.OS === 'android' ? PLAY_SUBSCRIPTIONS_URL : APPLE_SUBSCRIPTIONS_URL;
  const purchaseProvider = Platform.OS === 'android' ? 'google_play' : 'app_store';

  useEffect(() => {
    tokenRef.current = token;
  }, [token]);

  useEffect(() => {
    onRequireAccountRef.current = onRequireAccount;
  }, [onRequireAccount]);

  useEffect(() => {
    (async () => {
      try {
        const data = await api.getBillingPlans(token);
        const safePlans = Array.isArray(data) && data.length ? data : DEFAULT_PLANS;
        setPlans(safePlans);
        if (safePlans.length) {
          const hasPreferred = safePlans.some((x) => x.key === 'six_month');
          setSelectedPlanKey(hasPreferred ? 'six_month' : safePlans[0].key);
        }
      } catch {
        setPlans(DEFAULT_PLANS);
        setSelectedPlanKey('six_month');
      }
    })();
  }, [token]);

  useEffect(() => {
    if (Platform.OS !== 'ios' && Platform.OS !== 'android') return undefined;
    let alive = true;
    let disconnected = false;

    const onPurchaseResult = async ({ responseCode, results, errorCode }: any) => {
      if (!alive) return;
      if (responseCode === InAppPurchases.IAPResponseCode.USER_CANCELED) {
        pendingPlanKeyRef.current = null;
        purchaseHandledRef.current = false;
        setBusy(false);
        return;
      }
      if (responseCode !== InAppPurchases.IAPResponseCode.OK) {
        pendingPlanKeyRef.current = null;
        purchaseHandledRef.current = false;
        setBusy(false);
        if (errorCode) {
          Alert.alert('Purchase failed', String(errorCode));
        }
        return;
      }

      const purchases = Array.isArray(results) ? results : [];
      if (purchases.length === 0) {
        pendingPlanKeyRef.current = null;
        purchaseHandledRef.current = false;
        setBusy(false);
        return;
      }

      for (const purchase of purchases) {
        try {
          await InAppPurchases.finishTransactionAsync(purchase, false);
        } catch {
          // no-op
        }
      }

      if (purchaseHandledRef.current) return;
      purchaseHandledRef.current = true;

      const purchasedProduct = purchases
        .map((purchase) => String(purchase?.productId || purchase?.productIdentifier || ''))
        .find((id) => id && PRODUCT_ID_TO_PLAN[id]);

      const planKey = pendingPlanKeyRef.current || PRODUCT_ID_TO_PLAN[purchasedProduct || ''];
      const purchasedRecord = purchases.find((purchase) => {
        const productId = String(purchase?.productId || purchase?.productIdentifier || '');
        return productId && PRODUCT_ID_TO_PLAN[productId] === planKey;
      }) || purchases[0];
      pendingPlanKeyRef.current = null;

      if (!planKey) {
        purchaseHandledRef.current = false;
        setBusy(false);
        Alert.alert('Purchase failed', 'Purchased item is not mapped to a plan.');
        return;
      }

      const productId = readPurchaseField(purchasedRecord, ['productId', 'productIdentifier']) || PLAN_TO_PRODUCT_ID[planKey];
      const transactionId = readPurchaseField(purchasedRecord, ['transactionId', 'orderId', 'transaction_id']);
      const receiptData = readPurchaseField(purchasedRecord, ['transactionReceipt', 'receipt', 'receiptData']);
      const purchaseToken = readPurchaseField(purchasedRecord, ['purchaseToken', 'purchase_token', 'token']);
      if (!productId || (Platform.OS === 'ios' && !receiptData) || (Platform.OS === 'android' && !purchaseToken)) {
        purchaseHandledRef.current = false;
        setBusy(false);
        Alert.alert('Activation failed', 'Purchase proof is missing. Please try again.');
        return;
      }

      try {
        if (!tokenRef.current) {
          Alert.alert(
            'Purchase completed',
            'Your App Store subscription is active. Create an account to link this subscription to your AutoCutAI profile.',
            [
              {
                text: 'Later',
                style: 'cancel',
                onPress: () => navigation.goBack(),
              },
              {
                text: 'Create account',
                onPress: () => {
                  navigation.goBack();
                  if (typeof onRequireAccountRef.current === 'function') onRequireAccountRef.current();
                },
              },
            ],
          );
          return;
        }

        await api.subscribePremium(tokenRef.current, {
          plan_key: planKey,
          purchase_provider: purchaseProvider,
          product_id: productId,
          transaction_id: transactionId || undefined,
          receipt_data: Platform.OS === 'ios' ? receiptData : undefined,
          purchase_token: Platform.OS === 'android' ? purchaseToken : undefined,
        });
        Alert.alert('Success', 'Premium activated.');
        navigation.goBack();
      } catch (error: any) {
        Alert.alert('Activation failed', String(error?.message || error || 'Try again.'));
      } finally {
        purchaseHandledRef.current = false;
        setBusy(false);
      }
    };

    (async () => {
      try {
        try {
          await InAppPurchases.connectAsync();
        } catch (error) {
          if (!isAlreadyConnectedStoreError(error)) throw error;
        }
        if (!alive) return;
        InAppPurchases.setPurchaseListener(onPurchaseResult);
        const productIds = Object.values(PLAN_TO_PRODUCT_ID);
        const response = await InAppPurchases.getProductsAsync(productIds);
        if (!alive) return;
        if (response?.responseCode === InAppPurchases.IAPResponseCode.OK) {
          setStoreProductsById(mapStoreProducts(response.results));
        }
      } catch (error: any) {
        if (!alive) return;
        Alert.alert('Store unavailable', String(error?.message || error || 'Unable to connect to the store.'));
      }
    })();

    return () => {
      alive = false;
      pendingPlanKeyRef.current = null;
      purchaseHandledRef.current = false;
      InAppPurchases.setPurchaseListener(() => {});
      if (!disconnected) {
        disconnected = true;
        InAppPurchases.disconnectAsync().catch(() => {});
      }
    };
  }, []);

  const activePlan = useMemo(
    () => plans.find((plan) => plan.key === selectedPlanKey) || plans[0] || null,
    [plans, selectedPlanKey],
  );

  const subscribe = async () => {
    if (!activePlan || busy) return;

    if (Platform.OS === 'ios' || Platform.OS === 'android') {
      const productId = PLAN_TO_PRODUCT_ID[activePlan.key];
      if (!productId) {
        Alert.alert('Purchase failed', 'Selected plan is not configured for this store.');
        return;
      }

      try {
        setBusy(true);
        purchaseHandledRef.current = false;
        pendingPlanKeyRef.current = activePlan.key;
        try {
          await InAppPurchases.purchaseItemAsync(productId);
        } catch (firstError: any) {
          if (isAlreadyOwnedOrSubscribedError(firstError)) {
            pendingPlanKeyRef.current = null;
            purchaseHandledRef.current = false;
            setBusy(false);
            await openExternalLink(storeSubscriptionUrl);
            return;
          }
          const normalized = String(firstError?.message || firstError || '').toLowerCase();
          const requiresStoreQuery = normalized.includes('must query item')
            || normalized.includes('not available')
            || normalized.includes('not found');
          if (!requiresStoreQuery) throw firstError;
          const response = await InAppPurchases.getProductsAsync(Object.values(PLAN_TO_PRODUCT_ID));
          if (response?.responseCode === InAppPurchases.IAPResponseCode.OK) {
            const mapped = mapStoreProducts(response.results);
            if (Object.keys(mapped).length) {
              setStoreProductsById((prev) => ({ ...prev, ...mapped }));
            }
          }
          try {
            await InAppPurchases.purchaseItemAsync(productId);
          } catch (secondError: any) {
            if (isAlreadyOwnedOrSubscribedError(secondError)) {
              pendingPlanKeyRef.current = null;
              purchaseHandledRef.current = false;
              setBusy(false);
              await openExternalLink(storeSubscriptionUrl);
              return;
            }
            throw secondError;
          }
        }
      } catch (error: any) {
        pendingPlanKeyRef.current = null;
        purchaseHandledRef.current = false;
        setBusy(false);
        const text = String(error?.message || error || 'Try again.');
        const wasCancelled = text.toLowerCase().includes('cancel');
        if (!wasCancelled) {
          Alert.alert('Unable to subscribe', text);
        }
      }
      return;
    }

    try {
      setBusy(true);
      await api.subscribePremium(tokenRef.current, activePlan.key);
      Alert.alert('Success', 'Premium activated.');
      navigation.goBack();
    } catch (error: any) {
      Alert.alert('Unable to subscribe', String(error?.message || error || 'Try again.'));
    } finally {
      setBusy(false);
    }
  };

  const restorePurchases = async () => {
    if (busy) return;

    if (Platform.OS === 'ios' || Platform.OS === 'android') {
      try {
        setBusy(true);
        const response = await InAppPurchases.getPurchaseHistoryAsync();
        if (response?.responseCode !== InAppPurchases.IAPResponseCode.OK) {
          throw new Error(`Unable to read ${Platform.OS === 'android' ? 'Google Play' : 'App Store'} purchase history.`);
        }
        const history = Array.isArray(response.results) ? response.results : [];
        const knownPurchases = history
          .map((purchase: any) => String(purchase?.productId || purchase?.productIdentifier || ''))
          .filter((productId) => Boolean(PRODUCT_ID_TO_PLAN[productId]));

        const restoredProductId = knownPurchases[knownPurchases.length - 1];
        const restoredPlan = PRODUCT_ID_TO_PLAN[restoredProductId || ''];
        if (!restoredPlan) {
          Alert.alert('Nothing to restore', 'No active App Store purchases were found for this account.');
          return;
        }
        const restoredMatches = history.filter(
          (purchase: any) => String(purchase?.productId || purchase?.productIdentifier || '') === restoredProductId,
        );
        const restoredRecord = restoredMatches[restoredMatches.length - 1];
        const receiptData = readPurchaseField(restoredRecord, ['transactionReceipt', 'receipt', 'receiptData']);
        const purchaseToken = readPurchaseField(restoredRecord, ['purchaseToken', 'purchase_token', 'token']);
        const transactionId = readPurchaseField(restoredRecord, ['transactionId', 'orderId', 'transaction_id']);
        if ((Platform.OS === 'ios' && !receiptData) || (Platform.OS === 'android' && !purchaseToken)) {
          Alert.alert('Restore failed', `${Platform.OS === 'android' ? 'Google Play purchase token' : 'App Store receipt'} is missing for this purchase.`);
          return;
        }

        await api.subscribePremium(tokenRef.current, {
          plan_key: restoredPlan,
          purchase_provider: purchaseProvider,
          product_id: restoredProductId,
          transaction_id: transactionId || undefined,
          receipt_data: Platform.OS === 'ios' ? receiptData : undefined,
          purchase_token: Platform.OS === 'android' ? purchaseToken : undefined,
        });
        Alert.alert('Restored', 'Your premium access is active.');
        navigation.goBack();
      } catch (error: any) {
        Alert.alert('Restore failed', String(error?.message || error || 'Try again.'));
      } finally {
        setBusy(false);
      }
      return;
    }

    try {
      setBusy(true);
      const status = await api.getBillingStatus(tokenRef.current);
      if (status?.is_premium) {
        Alert.alert('Restored', 'Your premium access is active.');
        navigation.goBack();
        return;
      }
      Alert.alert('Nothing to restore', 'No active purchases were found for this account.');
    } catch (error: any) {
      Alert.alert('Restore failed', String(error?.message || error || 'Try again.'));
    } finally {
      setBusy(false);
    }
  };

  const openExternalLink = async (url: string) => {
    try {
      const canOpen = await Linking.canOpenURL(url);
      if (!canOpen) {
        Alert.alert('Unavailable', 'Cannot open this link on this device.');
        return;
      }
      await Linking.openURL(url);
    } catch (error: any) {
      Alert.alert('Open failed', String(error?.message || error || 'Try again.'));
    }
  };

  return (
    <SafeAreaView style={styles.container} edges={['top']}>
      <View style={styles.header}>
        <Pressable style={styles.backButton} onPress={() => navigation.goBack()}>
          <MaterialCommunityIcons name="arrow-left" size={22} color="#17395f" />
        </Pressable>
        <Text style={styles.title}>Upgrade to Premium</Text>
      </View>
      <Text style={styles.subtitle}>Unlimited projects, advanced analytics, and full AI video workflow access.</Text>

      <FlatList
        data={plans}
        keyExtractor={(item) => item.key}
        contentContainerStyle={styles.plansList}
        renderItem={({ item }) => {
          const selected = item.key === activePlan?.key;
          const productId = PLAN_TO_PRODUCT_ID[item.key];
          const storeProduct = storeProductsById[productId];
          const pricePresentation = resolveDisplayedPrices(item, storeProduct);
          return (
            <Pressable
              style={[styles.planCard, selected && styles.planCardActive]}
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
                {pricePresentation.oldLabel ? (
                  <Text style={styles.planOldPrice}>{pricePresentation.oldLabel}</Text>
                ) : null}
                <Text style={styles.planPrice}>{pricePresentation.currentLabel}</Text>
                <Text style={styles.planPeriod}>/ {item.billing_period}</Text>
              </View>
              {getPlanBenefits().map((feature) => (
                <Text key={`${item.key}-${feature}`} style={styles.planFeature}>
                  • {feature}
                </Text>
              ))}
            </Pressable>
          );
        }}
        ListEmptyComponent={<Text style={styles.emptyText}>Plans are not available right now.</Text>}
      />

      <Pressable
        style={[styles.subscribeButton, (!activePlan || busy) && styles.disabled]}
        onPress={subscribe}
        disabled={!activePlan || busy}
      >
        <Text style={styles.subscribeButtonText}>{busy ? 'Subscribing...' : 'Subscribe'}</Text>
      </Pressable>
      <Pressable style={[styles.restoreButton, busy && styles.disabled]} onPress={restorePurchases} disabled={busy}>
        <Text style={styles.restoreButtonText}>Restore Purchases</Text>
      </Pressable>
      {Platform.OS === 'ios' ? (
        <Pressable style={[styles.manageButton, busy && styles.disabled]} onPress={() => openExternalLink(storeSubscriptionUrl)} disabled={busy}>
          <Text style={styles.manageButtonText}>Manage Subscription</Text>
        </Pressable>
      ) : null}
      <View style={styles.legalRow}>
        <Pressable onPress={() => openExternalLink(TERMS_OF_USE_URL)}>
          <Text style={styles.legalLink}>Terms of Use</Text>
        </Pressable>
        <Text style={styles.legalDivider}>•</Text>
        <Pressable onPress={() => openExternalLink(PRIVACY_POLICY_URL)}>
          <Text style={styles.legalLink}>Privacy Policy</Text>
        </Pressable>
      </View>
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: '#f5f9ff', paddingHorizontal: 16, paddingTop: 8, paddingBottom: 20 },
  header: { flexDirection: 'row', alignItems: 'center', gap: 10 },
  backButton: {
    width: 36,
    height: 36,
    borderRadius: 999,
    alignItems: 'center',
    justifyContent: 'center',
    backgroundColor: '#e6edf7',
  },
  title: { color: '#163d69', fontSize: 24, fontWeight: '800' },
  subtitle: { color: '#4f6483', marginTop: 8, marginBottom: 10 },
  plansList: { paddingBottom: 10, gap: 10 },
  planCard: {
    backgroundColor: '#fff',
    borderRadius: 12,
    borderWidth: 1,
    borderColor: '#d5ddd8',
    padding: 12,
  },
  planCardActive: { borderColor: '#173e75', borderWidth: 2 },
  planRow: { flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center' },
  planTitle: { fontSize: 17, fontWeight: '700', color: '#1d3f66' },
  discountBadge: { backgroundColor: '#173e75', borderRadius: 999, paddingHorizontal: 8, paddingVertical: 4 },
  discountText: { color: '#fff', fontWeight: '700', fontSize: 11 },
  planPriceRow: { marginTop: 6, flexDirection: 'row', alignItems: 'baseline', gap: 6 },
  planOldPrice: { color: '#8a9590', textDecorationLine: 'line-through', fontWeight: '600' },
  planPrice: { color: '#2a3752', fontWeight: '700', fontSize: 20 },
  planPeriod: { color: '#5a6782', fontWeight: '600' },
  planFeature: { marginTop: 4, color: '#4d596d' },
  emptyText: { color: '#5a6b64', textAlign: 'center', marginTop: 24, fontWeight: '600' },
  subscribeButton: {
    marginTop: 10,
    backgroundColor: '#173e75',
    borderRadius: 12,
    paddingVertical: 14,
    alignItems: 'center',
    justifyContent: 'center',
  },
  subscribeButtonText: { color: '#fff', fontWeight: '800', fontSize: 15 },
  restoreButton: {
    marginTop: 10,
    borderRadius: 12,
    borderWidth: 1,
    borderColor: '#afc1cf',
    backgroundColor: '#f0f5fb',
    paddingVertical: 12,
    alignItems: 'center',
    justifyContent: 'center',
  },
  restoreButtonText: { color: '#214267', fontWeight: '800', fontSize: 14 },
  manageButton: {
    marginTop: 8,
    borderRadius: 12,
    borderWidth: 1,
    borderColor: '#afc1cf',
    backgroundColor: '#fff',
    paddingVertical: 12,
    alignItems: 'center',
    justifyContent: 'center',
  },
  manageButtonText: { color: '#214267', fontWeight: '800', fontSize: 14 },
  legalRow: { marginTop: 14, flexDirection: 'row', alignItems: 'center', justifyContent: 'center', gap: 10 },
  legalLink: { color: '#24507f', fontWeight: '700', textDecorationLine: 'underline' },
  legalDivider: { color: '#6b7d93', fontWeight: '700' },
  disabled: { opacity: 0.6 },
});
