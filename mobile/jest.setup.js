import "@testing-library/jest-native/extend-expect";

jest.mock("@expo/vector-icons", () => {
  const React = require("react");
  const { Text } = require("react-native");
  return {
    MaterialCommunityIcons: ({ name }) => React.createElement(Text, null, `icon-${name}`),
  };
});

jest.mock("expo-in-app-purchases", () => ({
  IAPResponseCode: {
    OK: 0,
    USER_CANCELED: 1,
  },
  connectAsync: jest.fn().mockResolvedValue({}),
  disconnectAsync: jest.fn().mockResolvedValue({}),
  setPurchaseListener: jest.fn(),
  getProductsAsync: jest.fn().mockResolvedValue({ responseCode: 0, results: [] }),
  purchaseItemAsync: jest.fn().mockResolvedValue({}),
  finishTransactionAsync: jest.fn().mockResolvedValue({}),
}));

jest.mock("expo-apple-authentication", () => ({
  AppleAuthenticationButton: "AppleAuthenticationButton",
  AppleAuthenticationButtonType: { SIGN_IN: "SIGN_IN" },
  AppleAuthenticationButtonStyle: { BLACK: "BLACK" },
  AppleAuthenticationScope: {
    FULL_NAME: "FULL_NAME",
    EMAIL: "EMAIL",
  },
  signInAsync: jest.fn(),
}));

jest.mock("expo-auth-session/providers/google", () => ({
  useAuthRequest: jest.fn(() => [null, null, jest.fn()]),
}));

jest.mock("expo-web-browser", () => ({
  maybeCompleteAuthSession: jest.fn(),
}));

jest.mock("react-native-safe-area-context", () => {
  const React = require("react");
  const { View } = require("react-native");
  return {
    SafeAreaView: ({ children }) => React.createElement(View, null, children),
    SafeAreaProvider: ({ children }) => React.createElement(View, null, children),
    useSafeAreaInsets: () => ({ top: 0, bottom: 0, left: 0, right: 0 }),
  };
});
