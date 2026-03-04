import React, { useEffect, useMemo, useState } from 'react';
import { Alert, AppState, View } from 'react-native';
import { NavigationContainer } from '@react-navigation/native';
import { createBottomTabNavigator } from '@react-navigation/bottom-tabs';
import { createNativeStackNavigator } from '@react-navigation/native-stack';
import AsyncStorage from '@react-native-async-storage/async-storage';
import { MaterialCommunityIcons } from '@expo/vector-icons';
import { SafeAreaProvider, useSafeAreaInsets } from 'react-native-safe-area-context';
import * as DocumentPicker from 'expo-document-picker';
import * as FileSystem from 'expo-file-system';
import * as Sharing from 'expo-sharing';

import { api, AuthSession, downloadOutput, getTimeline, processProjectSync, createProject, SubscriptionPlan } from './src/api/client';
import LoginScreen from './src/screens/LoginScreen';
import OnboardingScreen from './src/screens/OnboardingScreen';
import PricingScreen from './src/screens/PricingScreen';
import ProjectsScreen from './src/screens/ProjectsScreen';
import CreateScreen from './src/screens/CreateScreen';
import TimelineScreen from './src/screens/TimelineScreen';
import ProfileScreen from './src/screens/ProfileScreen';

type LocalFile = {
  uri: string;
  name: string;
  mimeType?: string;
  recordedAt?: string;
};

const PRELOGIN_ONBOARDING_KEY = 'onboarding_done_guest_v1';
const Tab: any = createBottomTabNavigator();
const RootStack: any = createNativeStackNavigator();

function MainTabs({
  session,
  onLogout,
  onOpenPricing,
  files,
  canRun,
  status,
  summary,
  projectId,
  onPickVideos,
  onRunPipeline,
  onSaveFinalVideo,
}: {
  session: AuthSession;
  onLogout: () => Promise<void>;
  onOpenPricing: () => void;
  files: LocalFile[];
  canRun: boolean;
  status: string;
  summary: string;
  projectId: string | null;
  onPickVideos: () => Promise<void>;
  onRunPipeline: () => Promise<void>;
  onSaveFinalVideo: () => Promise<void>;
}) {
  const insets = useSafeAreaInsets();

  const tabIcon = (name: React.ComponentProps<typeof MaterialCommunityIcons>['name'], focused: boolean, color: string) => (
    <MaterialCommunityIcons name={name} size={22} color={focused ? '#031b33' : color} />
  );

  return (
    <Tab.Navigator
      initialRouteName="Projects"
      screenOptions={{
        headerShown: false,
        tabBarActiveTintColor: '#031b33',
        tabBarInactiveTintColor: '#60748a',
        tabBarStyle: {
          backgroundColor: '#eef5ff',
          borderTopColor: '#c7d7ea',
          borderTopWidth: 1,
          height: 56 + insets.bottom,
          paddingBottom: Math.max(insets.bottom, 8),
          paddingTop: 6,
        },
        tabBarLabelStyle: {
          fontSize: 12,
          fontWeight: '600',
        },
      }}
    >
      <Tab.Screen
        name="Projects"
        options={{ tabBarIcon: ({ focused, color }) => tabIcon('folder-multiple-outline', focused, color) }}
      >
        {(props) => <ProjectsScreen {...props} projectId={projectId} status={status} onGoCreate={() => props.navigation.navigate('Create')} />}
      </Tab.Screen>
      <Tab.Screen
        name="Create"
        options={{ tabBarIcon: ({ focused, color }) => tabIcon('movie-open-plus-outline', focused, color) }}
      >
        {(props) => <CreateScreen {...props} files={files} canRun={canRun} status={status} onPickVideos={onPickVideos} onRunPipeline={onRunPipeline} />}
      </Tab.Screen>
      <Tab.Screen
        name="Timeline"
        options={{ tabBarIcon: ({ focused, color }) => tabIcon('timeline-outline', focused, color) }}
      >
        {(props) => <TimelineScreen {...props} projectId={projectId} summary={summary} onSaveFinalVideo={onSaveFinalVideo} />}
      </Tab.Screen>
      <Tab.Screen
        name="Profile"
        options={{ tabBarIcon: ({ focused, color }) => tabIcon('account-circle-outline', focused, color) }}
      >
        {(props) => (
          <ProfileScreen
            {...props}
            user={session.user}
            onLogout={onLogout}
            onOpenPricing={onOpenPricing}
          />
        )}
      </Tab.Screen>
    </Tab.Navigator>
  );
}

export default function App() {
  const [session, setSession] = useState<AuthSession | null>(null);
  const [onboardingDone, setOnboardingDone] = useState(false);
  const [preloginOnboardingDone, setPreloginOnboardingDone] = useState(false);
  const [authBootstrapDone, setAuthBootstrapDone] = useState(false);

  const [files, setFiles] = useState<LocalFile[]>([]);
  const [status, setStatus] = useState('Idle');
  const [projectId, setProjectId] = useState<string | null>(null);
  const [summary, setSummary] = useState('');

  const canRun = useMemo(() => files.length > 0, [files]);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const raw = await AsyncStorage.getItem('session');
        if (!raw) {
          if (!cancelled) {
            setSession(null);
            setOnboardingDone(false);
            const guestDone = await AsyncStorage.getItem(PRELOGIN_ONBOARDING_KEY);
            setPreloginOnboardingDone(guestDone === '1');
          }
          return;
        }

        const parsed = JSON.parse(raw) as AuthSession;
        if (!parsed?.access_token) throw new Error('Missing access token');

        const me = await api.me(parsed.access_token);
        const hydratedSession: AuthSession = {
          ...parsed,
          user: me,
          user_id: me.id,
        };

        if (cancelled) return;
        setSession(hydratedSession);
        await AsyncStorage.setItem('session', JSON.stringify(hydratedSession));
        setOnboardingDone(Boolean(me.onboarding_completed));
      } catch {
        await AsyncStorage.removeItem('session');
        if (!cancelled) {
          setSession(null);
          setOnboardingDone(false);
          const guestDone = await AsyncStorage.getItem(PRELOGIN_ONBOARDING_KEY);
          setPreloginOnboardingDone(guestDone === '1');
        }
      } finally {
        if (!cancelled) setAuthBootstrapDone(true);
      }
    })();

    return () => {
      cancelled = true;
    };
  }, []);

  const onAuthed = async (payload: AuthSession) => {
    if (!payload?.access_token || !payload?.user?.id) {
      throw new Error('Invalid auth response');
    }
    setSession(payload);
    await AsyncStorage.setItem('session', JSON.stringify(payload));
    setOnboardingDone(Boolean(payload.user.onboarding_completed));
  };

  const onLogout = async () => {
    setSession(null);
    setOnboardingDone(false);
    await AsyncStorage.removeItem('session');
  };

  const finishOnboarding = async ({ plan }: { plan?: SubscriptionPlan } = {}) => {
    if (!session?.access_token) {
      await AsyncStorage.setItem(PRELOGIN_ONBOARDING_KEY, '1');
      setPreloginOnboardingDone(true);
      return;
    }

    const updated = await api.completeOnboarding(session.access_token, {
      goal: 'Create faster social edits',
      niche: 'AI video editor',
      preferred_edit_style: 'smart-cut',
    });

    let nextUser = updated;
    if (plan && plan !== 'free') {
      nextUser = await api.activatePayment(session.access_token, plan);
    }

    const nextSession: AuthSession = {
      ...session,
      user: nextUser,
      user_id: nextUser.id,
    };

    setSession(nextSession);
    await AsyncStorage.setItem('session', JSON.stringify(nextSession));
    setOnboardingDone(true);
  };

  const pickVideos = async () => {
    try {
      const result = await DocumentPicker.getDocumentAsync({
        type: ['video/*'],
        multiple: true,
        copyToCacheDirectory: true,
      });

      if (result.canceled) return;

      const next = result.assets.map((asset) => ({
        uri: asset.uri,
        name: asset.name,
        mimeType: asset.mimeType,
        recordedAt: new Date().toISOString(),
      }));

      setFiles(next);
      setStatus(`Selected ${next.length} videos`);
    } catch (err: any) {
      Alert.alert('Selection failed', err?.message || 'Unable to pick videos.');
    }
  };

  const runPipeline = async () => {
    if (!canRun) return;

    try {
      setStatus('Uploading videos...');
      const created = await createProject({ name: `Project ${Date.now()}`, files });
      const id = created.project.id;
      setProjectId(id);

      setStatus('Processing auto pre-edit...');
      await processProjectSync(id);

      setStatus('Fetching timeline...');
      const timeline = await getTimeline(id);
      const gaps = timeline.pipeline.gap_ranges?.length || 0;
      const insertions = timeline.pipeline.insertion_suggestions?.length || 0;
      setSummary(`Tracks: ${timeline.tracks.length}, Gaps: ${gaps}, Insertions: ${insertions}`);
      setStatus('Completed');
    } catch (err: any) {
      Alert.alert('Pipeline failed', err?.message || 'Unknown error');
      setStatus('Error');
    }
  };

  const saveFinalVideo = async () => {
    if (!projectId) return;

    try {
      setStatus('Downloading final video...');
      const outputUrl = downloadOutput(projectId);
      const localUri = `${FileSystem.documentDirectory}final-${projectId}.mp4`;
      const out = await FileSystem.downloadAsync(outputUrl, localUri);
      setStatus('Saved locally');

      if (await Sharing.isAvailableAsync()) {
        await Sharing.shareAsync(out.uri);
      }
    } catch (err: any) {
      Alert.alert('Download failed', err?.message || 'Unknown error');
      setStatus('Error');
    }
  };

  useEffect(() => {
    let alive = true;
    const subscription = AppState.addEventListener('change', (nextState) => {
      if (!alive) return;
      if (nextState === 'active') {
        setStatus((prev) => (prev === 'Idle' ? 'Ready' : prev));
      }
    });
    return () => {
      alive = false;
      subscription.remove();
    };
  }, []);

  if (!authBootstrapDone) {
    return (
      <SafeAreaProvider>
        <View style={{ flex: 1, backgroundColor: '#f5f9ff' }} />
      </SafeAreaProvider>
    );
  }

  return (
    <SafeAreaProvider>
      <NavigationContainer>
        {session?.access_token ? (
          onboardingDone ? (
            <RootStack.Navigator screenOptions={{ headerShown: false }}>
              <RootStack.Screen name="Main">
                {(props) => (
                  <MainTabs
                    {...props}
                    session={session}
                    onLogout={onLogout}
                    onOpenPricing={() => props.navigation.navigate('Pricing')}
                    files={files}
                    canRun={canRun}
                    status={status}
                    summary={summary}
                    projectId={projectId}
                    onPickVideos={pickVideos}
                    onRunPipeline={runPipeline}
                    onSaveFinalVideo={saveFinalVideo}
                  />
                )}
              </RootStack.Screen>
              <RootStack.Screen name="Pricing">
                {(props) => <PricingScreen {...props} token={session.access_token} />}
              </RootStack.Screen>
            </RootStack.Navigator>
          ) : (
            <OnboardingScreen token={session.access_token} onDone={finishOnboarding} />
          )
        ) : !preloginOnboardingDone ? (
          <OnboardingScreen token={null} onDone={finishOnboarding} />
        ) : (
          <LoginScreen onAuthed={onAuthed} />
        )}
      </NavigationContainer>
    </SafeAreaProvider>
  );
}
