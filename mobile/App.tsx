import React, { useEffect, useMemo, useState } from 'react';
import { Alert, AppState, View } from 'react-native';
import { NavigationContainer } from '@react-navigation/native';
import { createBottomTabNavigator } from '@react-navigation/bottom-tabs';
import { createNativeStackNavigator } from '@react-navigation/native-stack';
import AsyncStorage from '@react-native-async-storage/async-storage';
import { MaterialCommunityIcons } from '@expo/vector-icons';
import { SafeAreaProvider, useSafeAreaInsets } from 'react-native-safe-area-context';

import { api, AuthSession } from './src/api/client';
import LoginScreen from './src/screens/LoginScreen';
import OnboardingScreen from './src/screens/OnboardingScreen';
import PricingScreen from './src/screens/PricingScreen';
import UploadScreen from './src/screens/UploadScreen';
import HistoryScreen from './src/screens/HistoryScreen';
import EditorScreen from './src/screens/EditorScreen';
import ProfileScreen from './src/screens/ProfileScreen';

const PRELOGIN_ONBOARDING_KEY = 'onboarding_done_guest_v1';
const Tab: any = createBottomTabNavigator();
const RootStack: any = createNativeStackNavigator();

function MainTabs({
  session,
  onLogout,
  onOpenPricing,
  onOpenProject,
}: {
  session: AuthSession;
  onLogout: () => Promise<void>;
  onOpenPricing: () => void;
  onOpenProject: (projectId: string) => void;
}) {
  const insets = useSafeAreaInsets();
  const tabIcon = (name: React.ComponentProps<typeof MaterialCommunityIcons>['name'], focused: boolean, color: string) => (
    <MaterialCommunityIcons name={name} size={22} color={focused ? '#173e75' : color} />
  );

  return (
    <Tab.Navigator
      initialRouteName="New"
      screenOptions={{
        headerShown: false,
        tabBarActiveTintColor: '#173e75',
        tabBarInactiveTintColor: '#60748a',
        tabBarStyle: {
          backgroundColor: '#eef5ff',
          borderTopColor: '#d2ddeb',
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
        name="New"
        options={{ tabBarIcon: ({ focused, color }) => tabIcon('movie-open-plus-outline', focused, color) }}
      >
        {() => <UploadScreen token={session.access_token} onProjectCreated={onOpenProject} />}
      </Tab.Screen>
      <Tab.Screen
        name="Projects"
        options={{
          tabBarIcon: ({ focused, color }) => tabIcon('folder-multiple-outline', focused, color),
          unmountOnBlur: true,
        }}
      >
        {() => <HistoryScreen token={session.access_token} onOpenProject={onOpenProject} />}
      </Tab.Screen>
      <Tab.Screen
        name="Profile"
        options={{ tabBarIcon: ({ focused, color }) => tabIcon('account-circle-outline', focused, color) }}
      >
        {(props: any) => (
          <ProfileScreen
            {...props}
            token={session.access_token}
            session={session}
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

  const [activeProjectId, setActiveProjectId] = useState<string | null>(null);

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
    setActiveProjectId(null);
    await AsyncStorage.removeItem('session');
  };

  const finishOnboarding = async () => {
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

    const nextSession: AuthSession = {
      ...session,
      user: updated,
      user_id: updated.id,
    };

    setSession(nextSession);
    await AsyncStorage.setItem('session', JSON.stringify(nextSession));
    setOnboardingDone(true);
  };

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
                {(props: any) => (
                  <MainTabs
                    {...props}
                    session={session}
                    onLogout={onLogout}
                    onOpenPricing={() => props.navigation.navigate('Pricing')}
                    onOpenProject={(projectId: string) => {
                      setActiveProjectId(projectId);
                      props.navigation.navigate('Editor');
                    }}
                  />
                )}
              </RootStack.Screen>
              <RootStack.Screen name="Editor">
                {(props: any) =>
                  activeProjectId ? (
                    <EditorScreen
                      token={session.access_token}
                      projectId={activeProjectId}
                      onClose={() => props.navigation.goBack()}
                    />
                  ) : null
                }
              </RootStack.Screen>
              <RootStack.Screen name="Pricing">
                {(props: any) => <PricingScreen {...props} token={session.access_token} onRequireAccount={onLogout} />}
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
