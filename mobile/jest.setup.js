import "@testing-library/jest-native/extend-expect";

jest.mock("@expo/vector-icons", () => {
  const React = require("react");
  const { Text } = require("react-native");
  return {
    MaterialCommunityIcons: ({ name }) => React.createElement(Text, null, `icon-${name}`),
  };
});

jest.mock("expo-image-picker", () => ({
  requestMediaLibraryPermissionsAsync: jest.fn().mockResolvedValue({ granted: true }),
  launchImageLibraryAsync: jest.fn().mockResolvedValue({ canceled: true, assets: [] }),
}));

jest.mock("expo-media-library", () => ({
  requestPermissionsAsync: jest.fn().mockResolvedValue({ granted: true }),
  saveToLibraryAsync: jest.fn().mockResolvedValue(undefined),
}));

jest.mock("expo-video", () => {
  const React = require("react");
  const { View } = require("react-native");
  return {
    useVideoPlayer: jest.fn(() => ({ loop: false, play: jest.fn(), pause: jest.fn() })),
    VideoView: (props) => React.createElement(View, props),
  };
});

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
