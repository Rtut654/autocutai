import React, { useState } from 'react';
import { View, Text, TextInput, Pressable, StyleSheet, Alert, Image, Platform } from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';
import * as AppleAuthentication from 'expo-apple-authentication';
import * as Google from 'expo-auth-session/providers/google';
import * as WebBrowser from 'expo-web-browser';
import { MaterialCommunityIcons } from '@expo/vector-icons';

import { api, AuthSession } from '../api/client';

WebBrowser.maybeCompleteAuthSession();

const normalizeClientId = (value?: string) => String(value || '').trim().replace(/^['"]|['"]$/g, '');
const IOS_GOOGLE_CLIENT_ID = normalizeClientId(process.env.EXPO_PUBLIC_GOOGLE_IOS_CLIENT_ID);
const ANDROID_GOOGLE_CLIENT_ID = normalizeClientId(process.env.EXPO_PUBLIC_GOOGLE_ANDROID_CLIENT_ID);
const WEB_GOOGLE_CLIENT_ID = normalizeClientId(process.env.EXPO_PUBLIC_GOOGLE_WEB_CLIENT_ID);
const IOS_GOOGLE_CLIENT_MATCH = IOS_GOOGLE_CLIENT_ID.match(/^(.+)\.apps\.googleusercontent\.com$/);
const IOS_GOOGLE_CLIENT_PREFIX = IOS_GOOGLE_CLIENT_MATCH ? IOS_GOOGLE_CLIENT_MATCH[1] : '';
const IOS_GOOGLE_REDIRECT_SCHEME = IOS_GOOGLE_CLIENT_PREFIX ? `com.googleusercontent.apps.${IOS_GOOGLE_CLIENT_PREFIX}` : '';
const IOS_GOOGLE_REDIRECT_URI = IOS_GOOGLE_REDIRECT_SCHEME ? `${IOS_GOOGLE_REDIRECT_SCHEME}:/oauthredirect` : undefined;

type Props = {
  onAuthed: (session: AuthSession) => Promise<void>;
};

export default function LoginScreen({ onAuthed }: Props) {
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [loading, setLoading] = useState(false);
  const [googleRequest, googleResponse, promptGoogle] = Google.useAuthRequest({
    iosClientId: IOS_GOOGLE_CLIENT_ID || undefined,
    androidClientId: ANDROID_GOOGLE_CLIENT_ID || undefined,
    webClientId: WEB_GOOGLE_CLIENT_ID || undefined,
    redirectUri: Platform.OS === 'ios' ? IOS_GOOGLE_REDIRECT_URI : undefined,
    scopes: ['openid', 'profile', 'email'],
  });

  const normalizeEmail = (value: string) => value.trim().toLowerCase();
  const isValidEmail = (value: string) => /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(value);

  const run = async (fn: () => Promise<AuthSession>) => {
    try {
      setLoading(true);
      const data = await fn();
      if (!data?.access_token || !data?.user?.id) {
        throw new Error('Invalid auth response');
      }
      await onAuthed(data);
    } catch (e: any) {
      Alert.alert('Auth error', String(e?.message || e));
    } finally {
      setLoading(false);
    }
  };

  const fetchGoogleProfile = async (accessToken?: string) => {
    const token = String(accessToken || '').trim();
    if (!token) return null;
    try {
      const response = await fetch('https://www.googleapis.com/oauth2/v3/userinfo', {
        headers: { Authorization: `Bearer ${token}` },
      });
      if (!response.ok) return null;
      const data = await response.json();
      return data && typeof data === 'object' ? data : null;
    } catch {
      return null;
    }
  };

  React.useEffect(() => {
    if (googleResponse?.type !== 'success') return;
    const idToken = googleResponse?.params?.id_token || googleResponse?.authentication?.idToken;
    const accessToken = googleResponse?.params?.access_token || googleResponse?.authentication?.accessToken;
    if (!idToken) {
      Alert.alert('Google sign-in failed', 'Missing Google ID token.');
      return;
    }
    run(async () => {
      const oauthProfile = await fetchGoogleProfile(accessToken);
      const oauthEmail = normalizeEmail(String(oauthProfile?.email || ''));
      return api.googleLogin({
        id_token: idToken,
        email: isValidEmail(oauthEmail) ? oauthEmail : undefined,
        name: String(
          oauthProfile?.name
            || [oauthProfile?.given_name, oauthProfile?.family_name].filter(Boolean).join(' ')
            || '',
        ).trim() || undefined,
        picture: String(oauthProfile?.picture || '').trim() || undefined,
        provider_user_id: String(oauthProfile?.sub || '').trim() || undefined,
      });
    });
  }, [googleResponse]);

  const runEmailSignin = async () => {
    const normalizedEmail = normalizeEmail(email);
    if (!isValidEmail(normalizedEmail) || !password) {
      Alert.alert('Invalid credentials', 'Enter a valid email and password.');
      return;
    }
    return run(() => api.login({ email: normalizedEmail, password }));
  };

  const runEmailSignup = async () => {
    const normalizedEmail = normalizeEmail(email);
    if (!isValidEmail(normalizedEmail) || !password) {
      Alert.alert('Invalid credentials', 'Enter a valid email and password.');
      return;
    }
    const fullName = (normalizedEmail.split('@')[0] || 'Creator').trim() || 'Creator';
    return run(() => api.signup({ email: normalizedEmail, password, full_name: fullName }));
  };

  const runGoogleLogin = async () => {
    const configuredForPlatform = Platform.OS === 'ios'
      ? Boolean(IOS_GOOGLE_CLIENT_ID)
      : Platform.OS === 'android'
        ? Boolean(ANDROID_GOOGLE_CLIENT_ID)
        : Boolean(WEB_GOOGLE_CLIENT_ID);
    if (!configuredForPlatform || !googleRequest) {
      Alert.alert('Google sign-in unavailable', 'Google client IDs are not configured for this platform.');
      return;
    }
    await promptGoogle();
  };

  const runAppleLogin = async () => {
    const normalizedEmail = normalizeEmail(email);
    const derivedName = (normalizedEmail.split('@')[0] || 'Creator').trim() || 'Creator';
    if (Platform.OS !== 'ios') {
      Alert.alert('Unavailable', 'Apple login is only available on iOS devices.');
      return;
    }

    return run(async () => {
      const credential = await AppleAuthentication.signInAsync({
        requestedScopes: [
          AppleAuthentication.AppleAuthenticationScope.FULL_NAME,
          AppleAuthentication.AppleAuthenticationScope.EMAIL,
        ],
      });
      if (!credential.authorizationCode && !credential.identityToken) {
        throw new Error('Apple sign-in did not return authorization credentials.');
      }
      const providerEmail = normalizeEmail(credential.email || normalizedEmail);
      const providerName = credential.fullName?.givenName
        ? `${credential.fullName.givenName}${credential.fullName.familyName ? ` ${credential.fullName.familyName}` : ''}`
        : derivedName;
      return api.appleLogin({
        code: credential.authorizationCode || undefined,
        id_token: credential.identityToken || undefined,
        email: isValidEmail(providerEmail) ? providerEmail : undefined,
        name: providerName || undefined,
        provider_user_id: credential.user || undefined,
      });
    });
  };

  return (
    <SafeAreaView style={styles.container} edges={['top']}>
      <Image
        source={require('../../assets/logo_autocutai.png')}
        style={styles.logo}
        resizeMode="contain"
        accessibilityLabel="AutoCutAI logo"
      />
      <Text style={styles.title}>AutoCutAI</Text>
      <Text style={styles.subtitle}>Sign in to edit, auto-cut, and export AI video projects</Text>

      <TextInput style={styles.input} value={email} onChangeText={setEmail} placeholder="Email" autoCapitalize="none" />
      <TextInput
        style={styles.input}
        value={password}
        onChangeText={setPassword}
        placeholder="Password"
        secureTextEntry
      />

      <Pressable style={styles.button} disabled={loading} onPress={runEmailSignin}>
        <Text style={styles.buttonText}>Email Sign In</Text>
      </Pressable>
      <Pressable style={styles.secondaryButton} disabled={loading} onPress={runEmailSignup}>
        <Text style={styles.secondaryButtonText}>Email Sign Up</Text>
      </Pressable>

      <Pressable style={styles.socialButton} disabled={loading || !googleRequest} onPress={runGoogleLogin}>
        <View style={styles.socialButtonContent}>
          <MaterialCommunityIcons name="google" size={18} color="#28332f" />
          <Text style={styles.socialText}>Continue with Google</Text>
        </View>
      </Pressable>
      {Platform.OS === 'ios' ? (
        <AppleAuthentication.AppleAuthenticationButton
          buttonType={AppleAuthentication.AppleAuthenticationButtonType.SIGN_IN}
          buttonStyle={AppleAuthentication.AppleAuthenticationButtonStyle.BLACK}
          cornerRadius={12}
          style={styles.appleButton}
          onPress={runAppleLogin}
        />
      ) : null}
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: '#f7f8f5', justifyContent: 'center', padding: 20, gap: 12 },
  logo: { width: 150, height: 150, alignSelf: 'center', marginBottom: 6 },
  title: { fontSize: 34, fontWeight: '700', color: '#101212' },
  subtitle: { fontSize: 15, color: '#55605c', marginBottom: 8 },
  input: { borderWidth: 1, borderColor: '#d5dbd7', backgroundColor: '#fff', borderRadius: 12, padding: 12 },
  button: { backgroundColor: '#111', borderRadius: 12, padding: 14, alignItems: 'center' },
  buttonText: { color: '#fff', fontWeight: '600' },
  secondaryButton: { backgroundColor: '#dfe7e1', borderRadius: 12, padding: 14, alignItems: 'center' },
  secondaryButtonText: { color: '#16211b', fontWeight: '600' },
  socialButton: { borderWidth: 1, borderColor: '#bac5bf', borderRadius: 12, padding: 12, alignItems: 'center' },
  socialButtonContent: { flexDirection: 'row', alignItems: 'center', gap: 8 },
  socialText: { color: '#28332f', fontWeight: '500' },
  appleButton: { width: '100%', height: 46, marginTop: 2 },
});
