/**
 * Placeholder for in-app purchases.
 *
 * `expo-in-app-purchases` was archived by Expo and does not build on SDK 53,
 * so it has been removed. Nothing is lost yet: the previous integration read
 * the App Store receipt and then discarded it, calling an endpoint that
 * granted a subscription without any verification, so it never actually
 * gated anything.
 *
 * This module keeps the same shape the screens were written against so the
 * UI still renders, and makes every purchase path fail loudly rather than
 * silently pretending to succeed.
 *
 * Replacing it properly means:
 *   1. RevenueCat (or react-native-iap) for StoreKit 2 on the client.
 *   2. Server-side receipt validation on the backend, driven by App Store
 *      Server Notifications rather than a client call.
 *   3. Deleting POST /api/auth/payments/activate, which currently lets any
 *      authenticated user grant themselves a paid plan.
 *   4. An entitlement check at the API boundary for each paid feature.
 */

export const BILLING_UNAVAILABLE_MESSAGE =
  'Purchases are not enabled in this build yet.';

export const IAPResponseCode = {
  OK: 0,
  USER_CANCELED: 1,
  ERROR: 2,
  DEFERRED: 3,
} as const;

export type PurchaseListener = (result: {
  responseCode: number;
  results?: unknown[];
  errorCode?: number;
}) => void;

/** True when a real billing provider is wired up. */
export function isBillingAvailable(): boolean {
  return false;
}

export async function connectAsync(): Promise<void> {
  // No provider connected.
}

export async function disconnectAsync(): Promise<void> {
  // Nothing to disconnect.
}

export function setPurchaseListener(_listener: PurchaseListener): void {
  // No provider emits purchases.
}

export async function getProductsAsync(
  _productIds: string[],
): Promise<{ responseCode: number; results: unknown[] }> {
  return { responseCode: IAPResponseCode.ERROR, results: [] };
}

export async function purchaseItemAsync(_productId: string): Promise<never> {
  throw new Error(BILLING_UNAVAILABLE_MESSAGE);
}

export async function getPurchaseHistoryAsync(): Promise<{
  responseCode: number;
  results: unknown[];
}> {
  return { responseCode: IAPResponseCode.ERROR, results: [] };
}

export async function finishTransactionAsync(_purchase: unknown, _consume: boolean): Promise<void> {
  // Nothing to finish.
}
