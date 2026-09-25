import React, { useEffect, useMemo, useRef, useState } from 'react';
import { View, Text, StyleSheet, Pressable, FlatList, Image, useWindowDimensions, Platform, Alert, Linking } from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';
import { MaterialCommunityIcons } from '@expo/vector-icons';
import * as InAppPurchases from '../billing/purchases';

import { api } from '../api/client';

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

function getPlanBenefits() {
  return PREMIUM_BENEFITS;
}

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

function isAlreadyOwnedOrSubscribedError(error: any) {
  const normalized = String(error?.message || error || '').toLowerCase();
  return normalized.includes('already')
    && (normalized.includes('owned') || normalized.includes('subscribed') || normalized.includes('purchased'));
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

function readPurchaseField(purchase: any, keys: string[]) {
  for (const key of keys) {
    const value = purchase?.[key];
    if (typeof value === 'string' && value.trim()) return value.trim();
  }
  return '';
}

const slides = [
  {
    key: 's1',
    title: 'Cut Dead Air Automatically',
    text: '',
    imageSource: require('../../onboarding/1.png'),
    imageAspectRatio: 1179 / 1941,
    imageZoom: 1.14,
  },
  {
    key: 's2',
    title: 'Build Better Edits Faster',
    text: '',
    imageSource: require('../../onboarding/2.png'),
    imageAspectRatio: 1179 / 2109,
    imageZoom: 1.14,
  },
  {
    key: 's3',
    title: 'Ship Social Clips at Scale',
    text: '',
    imageSource: require('../../onboarding/3.png'),
    imageAspectRatio: 1024 / 1269,
  },
  {
    key: 's4',
    title: 'Join the AutoCutAI Creator Workflow',
    text: '',
    imageSource: require('../../onboarding/4.png'),
    imageAspectRatio: 1179 / 1889,
    imageZoom: 1.14,
  },
];

type Props = {
  token: string | null;
  onDone: () => void | Promise<void>;
};

export default function OnboardingScreen({ token, onDone }: Props) {
  const { width: screenWidth, height: screenHeight } = useWindowDimensions();
  const carouselRef = useRef<FlatList<any> | null>(null);
  const tokenRef = useRef(token);
  const onDoneRef = useRef(onDone);
  const [page, setPage] = useState(0);
  const [plans, setPlans] = useState<any[]>([]);
  const [selectedPlanKey, setSelectedPlanKey] = useState('six_month');
  const [busy, setBusy] = useState(false);
  const [storeProductsById, setStoreProductsById] = useState<Record<string, any>>({});
  const pendingPlanKeyRef = useRef<string | null>(null);
  const purchaseHandledRef = useRef(false);
  const purchaseProvider = Platform.OS === 'android' ? 'google_play' : 'app_store';
  const storeSubscriptionUrl = Platform.OS === 'android' ? PLAY_SUBSCRIPTIONS_URL : APPLE_SUBSCRIPTIONS_URL;

  useEffect(() => {
    tokenRef.current = token;
  }, [token]);

  useEffect(() => {
    onDoneRef.current = onDone;
  }, [onDone]);

  useEffect(() => {
    (async () => {
      try {
        const p = await api.getBillingPlans(token);
        const safePlans = Array.isArray(p) && p.length ? p : DEFAULT_PLANS;
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
            `Your ${Platform.OS === 'android' ? 'Google Play' : 'App Store'} subscription is active.`,
            [{ text: 'OK', onPress: () => onDoneRef.current?.() }],
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
        onDoneRef.current?.();
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
        const response = await InAppPurchases.getProductsAsync(Object.values(PLAN_TO_PRODUCT_ID));
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

  const isLast = page === slides.length;
  const slideWidth = useMemo(() => Math.max(280, Math.round(screenWidth - 40)), [screenWidth]);
  const heroHeight = useMemo(
    () => Math.min(440, Math.max(300, Math.round(screenHeight * 0.5))),
    [screenHeight],
  );
  const activePlan = useMemo(() => plans.find((p) => p.key === selectedPlanKey) || plans[0], [plans, selectedPlanKey]);
  const openPricingStep = () => setPage(slides.length);

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

    if (!token) {
      onDone();
      return;
    }

    try {
      setBusy(true);
      await api.subscribePremium(token, activePlan.key);
      onDone();
    } catch (error: any) {
      Alert.alert('Unable to subscribe', String(error?.message || error || 'Try again.'));
    } finally {
      setBusy(false);
    }
  };

  const renderSlideHero = (slide: any) => {
    const fallbackAspect = 0.62;
    const targetAspect = Number(slide?.imageAspectRatio) > 0 ? Number(slide.imageAspectRatio) : fallbackAspect;
    const heroWidth = slideWidth;
    const imageZoom = Number(slide?.imageZoom) > 1 ? Number(slide.imageZoom) : 1;
    if (slide.imageSource) {
      return (
        <View style={[styles.hero, { height: heroHeight, width: heroWidth }]}> 
          <Image
            source={slide.imageSource}
            style={[
              styles.heroImage,
              {
                transform: [{ scale: imageZoom }],
                aspectRatio: targetAspect,
              },
            ]}
            resizeMode="cover"
          />
        </View>
      );
    }
    return (
      <View style={[styles.hero, styles.comingSoonWrap, { height: heroHeight, width: heroWidth }]}> 
        <MaterialCommunityIcons name="image-outline" size={38} color="#315d50" />
        <Text style={styles.comingSoonText}>Image coming soon</Text>
      </View>
    );
  };

  return (
    <SafeAreaView style={styles.container} edges={['top']}>
      {!isLast ? (
        <View style={styles.slideContent}>
          <FlatList
            ref={carouselRef}
            data={slides}
            keyExtractor={(item) => item.key}
            horizontal
            pagingEnabled
            bounces={false}
            showsHorizontalScrollIndicator={false}
            initialScrollIndex={Math.max(0, Math.min(slides.length - 1, page))}
            getItemLayout={(_data, index) => ({
              length: slideWidth,
              offset: slideWidth * index,
              index,
            })}
            onMomentumScrollEnd={(event) => {
              const offsetX = Number(event?.nativeEvent?.contentOffset?.x || 0);
              const nextIndex = Math.max(0, Math.min(slides.length - 1, Math.round(offsetX / Math.max(slideWidth, 1))));
              setPage(nextIndex);
            }}
            onScrollToIndexFailed={() => {}}
            renderItem={({ item }) => (
              <View style={[styles.slidePage, { width: slideWidth }]}> 
                {renderSlideHero(item)}
                <Text style={styles.title}>{item.title}</Text>
                {item.text ? <Text style={styles.text}>{item.text}</Text> : null}
              </View>
            )}
          />
          <View style={styles.slideBottom}>
            <View style={styles.dots}>
              {slides.map((_, i) => (
                <View key={i} style={[styles.dot, page === i && styles.dotActive]} />
              ))}
            </View>
            <Pressable
              style={styles.button}
              onPress={() => {
                if (page >= slides.length - 1) {
                  openPricingStep();
                  return;
                }
                const nextIndex = page + 1;
                carouselRef.current?.scrollToIndex?.({ index: nextIndex, animated: true });
                setPage(nextIndex);
              }}
            >
              <Text style={styles.buttonText}>Next</Text>
            </Pressable>
            <Pressable onPress={openPricingStep} style={styles.linkWrap}>
              <Text style={styles.linkText}>Skip</Text>
            </Pressable>
          </View>
        </View>
      ) : (
        <>
          <Text style={styles.title}>Unlock AutoCutAI Premium</Text>
          <Text style={styles.text}>Unlimited projects, advanced analytics, and full AI editing access.</Text>
          <FlatList
            data={plans}
            keyExtractor={(item) => item.key}
            renderItem={({ item }) => {
              const productId = PLAN_TO_PRODUCT_ID[item.key];
              const storeProduct = storeProductsById[productId];
              const pricePresentation = resolveDisplayedPrices(item, storeProduct);
              return (
                <Pressable style={[styles.planCard, item.key === activePlan?.key && styles.planCardActive]} onPress={() => setSelectedPlanKey(item.key)}>
                  <View style={styles.planRow}>
                    <Text style={styles.planTitle}>{item.title}</Text>
                    {item.discount_label ? (
                      <View style={styles.discountBadge}>
                        <Text style={styles.discountText}>{item.discount_label}</Text>
                      </View>
                    ) : null}
                  </View>
                  <View style={styles.planPriceRow}>
                    {pricePresentation.oldLabel ? <Text style={styles.planOldPrice}>{pricePresentation.oldLabel}</Text> : null}
                    <Text style={styles.planPrice}>{pricePresentation.currentLabel}</Text>
                    <Text style={styles.planPeriod}>/ {item.billing_period}</Text>
                  </View>
                  {getPlanBenefits().map((f) => (
                    <Text key={f} style={styles.planFeature}>• {f}</Text>
                  ))}
                </Pressable>
              );
            }}
          />
          <Pressable style={styles.button} onPress={subscribe}>
            <Text style={styles.buttonText}>{busy ? 'Subscribing...' : 'Subscribe'}</Text>
          </Pressable>
          {Platform.OS === 'ios' ? (
            <Pressable style={[styles.manageButton, busy && styles.disabled]} disabled={busy} onPress={() => openExternalLink(storeSubscriptionUrl)}>
              <Text style={styles.manageButtonText}>Manage Subscription</Text>
            </Pressable>
          ) : null}
          <Pressable onPress={onDone} style={styles.linkWrap}>
            <Text style={styles.linkText}>Continue Free</Text>
          </Pressable>
        </>
      )}
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: '#f5f9ff', padding: 20, paddingTop: 44 },
  slideContent: { flex: 1 },
  slidePage: { alignItems: 'center' },
  hero: {
    borderRadius: 18,
    borderWidth: 1,
    borderColor: '#c9d9ee',
    backgroundColor: '#ffffff',
    marginBottom: 24,
    overflow: 'hidden',
    alignItems: 'center',
    justifyContent: 'center',
  },
  heroImage: {
    width: '100%',
    height: '100%',
  },
  comingSoonWrap: {
    alignItems: 'center',
    justifyContent: 'center',
    gap: 10,
    backgroundColor: '#dcebe4',
  },
  comingSoonText: {
    color: '#315d50',
    fontWeight: '700',
    fontSize: 16,
  },
  title: { marginTop: 8, fontSize: 31, fontWeight: '700', color: '#11213a' },
  text: { marginTop: 10, color: '#4f5f78', fontSize: 15, lineHeight: 21 },
  slideBottom: { marginTop: 'auto', paddingTop: 22, paddingBottom: 8 },
  dots: { flexDirection: 'row', gap: 8 },
  dot: { width: 8, height: 8, borderRadius: 999, backgroundColor: '#cad2cc' },
  dotActive: { width: 22, backgroundColor: '#1b3f7a' },
  button: { marginTop: 20, backgroundColor: '#173e75', borderRadius: 12, padding: 14, alignItems: 'center' },
  buttonText: { color: '#fff', fontWeight: '700' },
  manageButton: {
    marginTop: 8,
    borderRadius: 12,
    borderWidth: 1,
    borderColor: '#bac5bf',
    backgroundColor: '#fff',
    padding: 12,
    alignItems: 'center',
  },
  manageButtonText: { color: '#23395c', fontWeight: '700' },
  linkWrap: { marginTop: 12, alignItems: 'center' },
  linkText: { color: '#2b4468', fontWeight: '600' },
  planCard: { marginTop: 12, backgroundColor: '#fff', borderRadius: 12, borderWidth: 1, borderColor: '#d5ddd8', padding: 12 },
  planCardActive: { borderColor: '#173e75', borderWidth: 2 },
  planRow: { flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center' },
  planTitle: { fontSize: 17, fontWeight: '700', color: '#173e75' },
  discountBadge: { backgroundColor: '#173e75', borderRadius: 999, paddingHorizontal: 8, paddingVertical: 4 },
  discountText: { color: '#fff', fontWeight: '700', fontSize: 11 },
  planPriceRow: { marginTop: 6, flexDirection: 'row', alignItems: 'baseline', gap: 6 },
  planOldPrice: { color: '#8a9590', textDecorationLine: 'line-through', fontWeight: '600' },
  planPrice: { color: '#2a3d57', fontWeight: '700', fontSize: 20 },
  planPeriod: { color: '#5a6780', fontWeight: '600' },
  planFeature: { marginTop: 4, color: '#4d596c' },
  disabled: { opacity: 0.6 },
});
