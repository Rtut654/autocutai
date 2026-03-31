import React from 'react';
import { describe, expect, it, jest } from '@jest/globals';
import { render } from '@testing-library/react-native';

import LoginScreen from '../LoginScreen';
import ProfileScreen from '../ProfileScreen';

describe('mobile auth screens', () => {
  it('renders login screen', () => {
    const { getByText } = render(<LoginScreen onAuthed={jest.fn(async () => {})} />);
    expect(getByText('AutoCutAI')).toBeTruthy();
    expect(getByText('Email Sign In')).toBeTruthy();
  });

  it('renders profile screen', () => {
    const { getByText, getAllByText } = render(
      <ProfileScreen
        token="token"
        session={{
          access_token: 'token',
          token_type: 'bearer',
          user_id: 'user-1',
          user: {
            id: 'user-1',
            email: 'creator@example.com',
            full_name: 'Creator',
            onboarding_completed: true,
            subscription_plan: 'free',
            provider: 'email',
          },
        }}
        onLogout={jest.fn(async () => {})}
        onOpenPricing={jest.fn()}
      />,
    );
    expect(getByText('Profile')).toBeTruthy();
    expect(getAllByText('Upgrade to Premium').length).toBeGreaterThan(0);
  });
});
