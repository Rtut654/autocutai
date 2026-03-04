import React, { useState } from 'react';
import { View, Text, TextInput, Pressable, StyleSheet, Alert } from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';
import { MaterialCommunityIcons } from '@expo/vector-icons';

import { api, AuthSession } from '../api/client';

type Props = {
  onAuthed: (session: AuthSession) => Promise<void>;
};

export default function LoginScreen({ onAuthed }: Props) {
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [loading, setLoading] = useState(false);

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

  const showSocialUnavailable = (provider: string) => {
    Alert.alert('Unavailable', `${provider} sign in is not configured yet for this build.`);
  };

  return (
    <SafeAreaView style={styles.container} edges={['top']}>
      <View style={styles.logoWrap}>
        <MaterialCommunityIcons name="movie-open-outline" size={38} color="#031b33" />
      </View>
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

      <Pressable style={styles.socialButton} disabled={loading} onPress={() => showSocialUnavailable('Google')}>
        <View style={styles.socialButtonContent}>
          <MaterialCommunityIcons name="google" size={18} color="#163a5e" />
          <Text style={styles.socialText}>Continue with Google</Text>
        </View>
      </Pressable>

      <Pressable style={styles.socialButton} disabled={loading} onPress={() => showSocialUnavailable('Apple')}>
        <View style={styles.socialButtonContent}>
          <MaterialCommunityIcons name="apple" size={18} color="#163a5e" />
          <Text style={styles.socialText}>Continue with Apple</Text>
        </View>
      </Pressable>
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: '#f5f9ff', justifyContent: 'center', padding: 20, gap: 12 },
  logoWrap: {
    width: 80,
    height: 80,
    borderRadius: 20,
    backgroundColor: '#d8ebff',
    borderWidth: 1,
    borderColor: '#a2c6ea',
    alignItems: 'center',
    justifyContent: 'center',
    alignSelf: 'center',
    marginBottom: 6,
  },
  title: { fontSize: 34, fontWeight: '700', color: '#031b33' },
  subtitle: { fontSize: 15, color: '#4c6077', marginBottom: 8 },
  input: { borderWidth: 1, borderColor: '#c5d7ea', backgroundColor: '#fff', borderRadius: 12, padding: 12 },
  button: { backgroundColor: '#031b33', borderRadius: 12, padding: 14, alignItems: 'center' },
  buttonText: { color: '#fff', fontWeight: '600' },
  secondaryButton: { backgroundColor: '#dce9f7', borderRadius: 12, padding: 14, alignItems: 'center' },
  secondaryButtonText: { color: '#0d2d4b', fontWeight: '600' },
  socialButton: { borderWidth: 1, borderColor: '#b2c9e0', borderRadius: 12, padding: 12, alignItems: 'center' },
  socialButtonContent: { flexDirection: 'row', alignItems: 'center', gap: 8 },
  socialText: { color: '#163a5e', fontWeight: '500' },
});
