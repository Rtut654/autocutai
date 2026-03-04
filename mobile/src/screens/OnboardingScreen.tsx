import React, { useMemo, useRef, useState } from 'react';
import { View, Text, StyleSheet, Pressable, FlatList, useWindowDimensions, Alert } from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';
import { MaterialCommunityIcons } from '@expo/vector-icons';

import { api, SubscriptionPlan } from '../api/client';

type Props = {
  token: string | null;
  onDone: (params?: { plan?: SubscriptionPlan }) => Promise<void>;
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
  'Priority render queue for faster output',
  'Advanced subtitles and silence cleanup',
  'B-roll and insert suggestions',
  'Cloud project sync and version restore',
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

const slides = [
  {
    key: 's1',
    title: 'Import Clips and Auto-Detect Highlights',
    text: 'AutoCutAI finds pauses, dead air, and key moments in minutes.',
    icon: 'content-cut' as const,
  },
  {
    key: 's2',
    title: 'Smart Timeline Suggestions',
    text: 'Get AI-generated cut points and insert prompts for engaging edits.',
    icon: 'timeline-clock-outline' as const,
  },
  {
    key: 's3',
    title: 'Publish Faster',
    text: 'Export polished vertical and horizontal versions in one flow.',
    icon: 'video-outline' as const,
  },
  {
    key: 's4',
    title: 'Scale Your Editing Workflow',
    text: 'Use reusable presets and cloud processing for every new project.',
    icon: 'rocket-launch-outline' as const,
  },
];

export default function OnboardingScreen({ token, onDone }: Props) {
  const { width: screenWidth, height: screenHeight } = useWindowDimensions();
  const carouselRef = useRef<FlatList<any> | null>(null);
  const [page, setPage] = useState(0);
  const [plans] = useState(DEFAULT_PLANS);
  const [selectedPlanKey, setSelectedPlanKey] = useState<SubscriptionPlan>('pro_yearly');
  const [busy, setBusy] = useState(false);

  const isLast = page === slides.length;
  const slideWidth = useMemo(() => Math.max(280, Math.round(screenWidth - 40)), [screenWidth]);
  const heroHeight = useMemo(() => Math.min(440, Math.max(300, Math.round(screenHeight * 0.5))), [screenHeight]);
  const activePlan = useMemo(
    () => plans.find((p) => p.key === selectedPlanKey) || plans[0],
    [plans, selectedPlanKey],
  );
  const openPricingStep = () => setPage(slides.length);

  const subscribe = async () => {
    if (!activePlan || busy) return;
    if (!token) {
      await onDone({ plan: activePlan.key });
      return;
    }

    try {
      setBusy(true);
      if (activePlan.key !== 'free') {
        await api.startPayment(token, activePlan.key);
        await api.activatePayment(token, activePlan.key);
      }
      await onDone({ plan: activePlan.key });
    } catch (error: any) {
      Alert.alert('Unable to subscribe', String(error?.message || error || 'Try again.'));
    } finally {
      setBusy(false);
    }
  };

  const renderSlideHero = (slide: (typeof slides)[number]) => (
    <View style={[styles.hero, { height: heroHeight, width: slideWidth }]}>
      <View style={styles.heroIconWrap}>
        <MaterialCommunityIcons name={slide.icon} size={46} color="#053560" />
      </View>
    </View>
  );

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
          <Text style={styles.title}>Unlock AutoCutAI Pro</Text>
          <Text style={styles.text}>Faster edits, more exports, and premium AI tools for every project.</Text>
          <FlatList
            data={plans}
            keyExtractor={(item) => item.key}
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
                {PREMIUM_BENEFITS.map((f) => (
                  <Text key={f} style={styles.planFeature}>• {f}</Text>
                ))}
              </Pressable>
            )}
          />
          <Pressable style={styles.button} onPress={subscribe}>
            <Text style={styles.buttonText}>{busy ? 'Activating...' : 'Subscribe'}</Text>
          </Pressable>
          <Pressable onPress={() => onDone({ plan: 'free' })} style={styles.linkWrap}>
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
    borderColor: '#b8d0e8',
    backgroundColor: '#ffffff',
    marginBottom: 24,
    overflow: 'hidden',
    alignItems: 'center',
    justifyContent: 'center',
  },
  heroIconWrap: {
    width: 122,
    height: 122,
    borderRadius: 999,
    backgroundColor: '#d8ebff',
    alignItems: 'center',
    justifyContent: 'center',
  },
  title: { marginTop: 8, fontSize: 31, fontWeight: '700', color: '#031b33' },
  text: { marginTop: 10, color: '#4c6077', fontSize: 15, lineHeight: 21 },
  slideBottom: { marginTop: 'auto', paddingTop: 22, paddingBottom: 8 },
  dots: { flexDirection: 'row', gap: 8 },
  dot: { width: 8, height: 8, borderRadius: 999, backgroundColor: '#c2d5e8' },
  dotActive: { width: 22, backgroundColor: '#031b33' },
  button: { marginTop: 20, backgroundColor: '#031b33', borderRadius: 12, padding: 14, alignItems: 'center' },
  buttonText: { color: '#fff', fontWeight: '700' },
  linkWrap: { marginTop: 12, alignItems: 'center' },
  linkText: { color: '#1f4467', fontWeight: '600' },
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
});
