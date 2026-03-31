import React, { useEffect, useState } from 'react';
import {
  View,
  Text,
  Pressable,
  StyleSheet,
  TextInput,
  ScrollView,
  Alert,
  Linking,
} from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';

import { api, AuthSession, MeUser } from '../api/client';

type Props = {
  token: string;
  session: AuthSession;
  onLogout: () => Promise<void>;
  onOpenPricing: () => void;
};

const SUPPORT_EMAIL = 'contact@autocutai.app';
const TERMS_OF_USE_URL = 'https://autocutai.app/terms-of-use';
const PRIVACY_POLICY_URL = 'https://autocutai.app/privacy-policy';

function planLabel(plan: string | undefined) {
  if (plan === 'pro_yearly') return 'Pro Yearly';
  if (plan === 'pro_monthly') return 'Pro Monthly';
  return 'Free';
}

function signInLabel(provider?: string | null) {
  if (provider === 'apple') return 'Apple';
  if (provider === 'google') return 'Google';
  return 'Email';
}

export default function ProfileScreen({ token, session, onLogout, onOpenPricing }: Props) {
  const [profile, setProfile] = useState<MeUser>(session.user);
  const [name, setName] = useState(String(session.user.full_name || '').trim());
  const [saving, setSaving] = useState(false);
  const [deletingAccount, setDeletingAccount] = useState(false);
  const isFree = profile.subscription_plan === 'free';

  useEffect(() => {
    setProfile(session.user);
    setName(String(session.user.full_name || '').trim());
  }, [session.user]);

  const saveProfile = async () => {
    const trimmedName = name.trim();
    if (!trimmedName) {
      Alert.alert('Name required', 'Please enter your name.');
      return;
    }
    try {
      setSaving(true);
      const updated = await api.updateProfile(token, { full_name: trimmedName });
      setProfile(updated);
      setName(String(updated.full_name || '').trim());
      Alert.alert('Saved', 'Profile updated.');
    } catch (error: any) {
      Alert.alert('Save failed', String(error?.message || error || 'Try again.'));
    } finally {
      setSaving(false);
    }
  };

  const openLink = async (url: string) => {
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

  const performDeleteAccount = async () => {
    if (deletingAccount) return;
    try {
      setDeletingAccount(true);
      await api.deleteAccount(token);
      Alert.alert('Account deleted', 'Your account and related data were removed.', [
        {
          text: 'OK',
          onPress: () => {
            void onLogout();
          },
        },
      ]);
    } catch (error: any) {
      Alert.alert('Delete failed', String(error?.message || error || 'Try again.'));
    } finally {
      setDeletingAccount(false);
    }
  };

  const confirmDeleteAccount = () => {
    Alert.alert(
      'Delete account',
      'Are you sure you want to permanently delete your account? This cannot be undone.',
      [
        { text: 'Cancel', style: 'cancel' },
        { text: 'Delete', style: 'destructive', onPress: performDeleteAccount },
      ],
    );
  };

  return (
    <SafeAreaView style={styles.container} edges={['top']}>
      <Text style={styles.title}>Profile</Text>
      <Text style={styles.subtitle}>Manage account details, billing, and legal settings.</Text>
      <ScrollView contentContainerStyle={styles.content} showsVerticalScrollIndicator={false}>
        {isFree ? (
          <Pressable style={styles.upgradeHero} onPress={onOpenPricing}>
            <Text style={styles.upgradeHeroTitle}>Upgrade to Premium</Text>
            <Text style={styles.upgradeHeroText}>
              Unlock unlimited AI video projects, faster export flows, and premium creator tools.
            </Text>
            <View style={styles.upgradeHeroButton}>
              <Text style={styles.upgradeHeroButtonText}>Open Pricing</Text>
            </View>
          </Pressable>
        ) : null}

        <View style={styles.card}>
          <Text style={styles.sectionTitle}>Account</Text>
          <Text style={styles.label}>Name</Text>
          <TextInput
            value={name}
            onChangeText={setName}
            placeholder="Your name"
            style={styles.input}
            autoCapitalize="words"
          />
          <Text style={styles.label}>Email</Text>
          <Text style={styles.value}>{profile.email}</Text>
          <Text style={styles.label}>Sign in with</Text>
          <Text style={styles.value}>{signInLabel(profile.provider)}</Text>
          <Pressable style={[styles.saveButton, saving && styles.disabled]} onPress={saveProfile} disabled={saving}>
            <Text style={styles.saveButtonText}>{saving ? 'Saving...' : 'Save Profile'}</Text>
          </Pressable>
        </View>

        <View style={styles.card}>
          <Text style={styles.sectionTitle}>Billing</Text>
          <Text style={styles.label}>Plan</Text>
          <Text style={styles.value}>{planLabel(profile.subscription_plan)}</Text>
          <Text style={styles.label}>Onboarding</Text>
          <Text style={styles.value}>{profile.onboarding_completed ? 'Completed' : 'Pending'}</Text>
          <Pressable style={styles.button} onPress={onOpenPricing}>
            <Text style={styles.buttonText}>{isFree ? 'Upgrade to Premium' : 'Manage Plan'}</Text>
          </Pressable>
        </View>

        <View style={styles.card}>
          <Text style={styles.sectionTitle}>Support</Text>
          <Text style={styles.supportHint}>Reach out if you need help with onboarding, billing, or exports.</Text>
          <Pressable style={styles.linkButton} onPress={() => openLink(`mailto:${SUPPORT_EMAIL}`)}>
            <Text style={styles.linkButtonText}>Email Support</Text>
          </Pressable>
        </View>

        <View style={styles.card}>
          <Text style={styles.sectionTitle}>Legal</Text>
          <Text style={styles.supportHint}>Review the latest terms and privacy policy.</Text>
          <View style={styles.row}>
            <Pressable style={styles.linkButton} onPress={() => openLink(TERMS_OF_USE_URL)}>
              <Text style={styles.linkButtonText}>Terms of Use</Text>
            </Pressable>
            <Pressable style={styles.linkButton} onPress={() => openLink(PRIVACY_POLICY_URL)}>
              <Text style={styles.linkButtonText}>Privacy Policy</Text>
            </Pressable>
          </View>
        </View>

        <Pressable
          style={[styles.deleteAccountButton, deletingAccount && styles.disabled]}
          onPress={confirmDeleteAccount}
          disabled={deletingAccount}
        >
          <Text style={styles.deleteAccountText}>{deletingAccount ? 'Deleting...' : 'Delete account'}</Text>
        </Pressable>

        <Pressable style={styles.logout} onPress={() => void onLogout()}>
          <Text style={styles.logoutText}>Log out</Text>
        </Pressable>
      </ScrollView>
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: '#f7f8f5', paddingHorizontal: 16 },
  title: { fontSize: 30, fontWeight: '700', color: '#111' },
  subtitle: { marginTop: 4, marginBottom: 8, color: '#5e6964' },
  content: { paddingBottom: 24 },
  upgradeHero: {
    marginTop: 8,
    borderRadius: 14,
    borderWidth: 1,
    borderColor: '#0f4a3a',
    backgroundColor: '#164b3c',
    padding: 12,
    gap: 8,
  },
  upgradeHeroTitle: { color: '#ffffff', fontSize: 18, fontWeight: '900' },
  upgradeHeroText: { color: '#e8f6f0', fontWeight: '600' },
  upgradeHeroButton: {
    alignSelf: 'flex-start',
    borderRadius: 999,
    backgroundColor: '#f4fbf7',
    paddingHorizontal: 12,
    paddingVertical: 6,
  },
  upgradeHeroButtonText: { color: '#1f5a48', fontWeight: '800' },
  card: { backgroundColor: '#fff', borderRadius: 12, padding: 14, marginTop: 12, borderWidth: 1, borderColor: '#d8dfda' },
  sectionTitle: { color: '#1d2b26', fontSize: 17, fontWeight: '800', marginBottom: 2 },
  label: { color: '#61706a', marginTop: 8 },
  value: { color: '#1d2420', fontWeight: '600', marginTop: 2 },
  input: {
    marginTop: 10,
    borderWidth: 1,
    borderColor: '#cad8d1',
    backgroundColor: '#f8fbf9',
    borderRadius: 10,
    minHeight: 44,
    paddingHorizontal: 10,
    color: '#1a2521',
  },
  saveButton: {
    marginTop: 12,
    borderRadius: 10,
    backgroundColor: '#164b3c',
    minHeight: 44,
    alignItems: 'center',
    justifyContent: 'center',
  },
  saveButtonText: { color: '#ffffff', fontWeight: '800' },
  button: {
    marginTop: 12,
    borderRadius: 10,
    backgroundColor: '#164b3c',
    minHeight: 44,
    alignItems: 'center',
    justifyContent: 'center',
  },
  buttonText: { color: '#ffffff', fontWeight: '800' },
  supportHint: { color: '#51635c', marginTop: 4, marginBottom: 10, fontWeight: '600' },
  row: { flexDirection: 'row', gap: 8 },
  linkButton: {
    flex: 1,
    minHeight: 44,
    borderRadius: 10,
    borderWidth: 1,
    borderColor: '#cad8d1',
    backgroundColor: '#f8fbf9',
    alignItems: 'center',
    justifyContent: 'center',
    paddingHorizontal: 12,
  },
  linkButtonText: { color: '#1f3a31', fontWeight: '700' },
  deleteAccountButton: {
    borderWidth: 1,
    borderColor: '#e8b3b3',
    backgroundColor: '#fff4f4',
    borderRadius: 12,
    padding: 12,
    alignItems: 'center',
    marginTop: 10,
  },
  deleteAccountText: { color: '#b42318', fontWeight: '700' },
  logout: { borderWidth: 1, borderColor: '#d2d9d4', borderRadius: 12, padding: 12, alignItems: 'center', marginTop: 10 },
  logoutText: { color: '#243029' },
  disabled: { opacity: 0.6 },
});
