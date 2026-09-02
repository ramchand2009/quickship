import * as SecureStore from 'expo-secure-store';

import type { StoredAuth } from './types';

const AUTH_KEY = 'mathukai.auth.v1';
const INSTALLATION_KEY = 'mathukai.installation-id.v1';
const PUSH_DEVICE_KEY = 'mathukai.push-device-id.v1';

function createInstallationId() {
  return 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g, (character) => {
    const random = Math.floor(Math.random() * 16);
    const value = character === 'x' ? random : (random & 0x3) | 0x8;
    return value.toString(16);
  });
}

export async function loadStoredAuth(): Promise<StoredAuth | null> {
  const value = await SecureStore.getItemAsync(AUTH_KEY);
  if (!value) return null;
  try {
    return JSON.parse(value) as StoredAuth;
  } catch {
    await SecureStore.deleteItemAsync(AUTH_KEY);
    return null;
  }
}

export async function saveStoredAuth(auth: StoredAuth) {
  await SecureStore.setItemAsync(AUTH_KEY, JSON.stringify(auth));
}

export async function clearStoredAuth() {
  await SecureStore.deleteItemAsync(AUTH_KEY);
}

export async function getInstallationId() {
  const existing = await SecureStore.getItemAsync(INSTALLATION_KEY);
  if (existing) return existing;
  const created = createInstallationId();
  await SecureStore.setItemAsync(INSTALLATION_KEY, created);
  return created;
}

export async function savePushDeviceId(deviceId: string) {
  await SecureStore.setItemAsync(PUSH_DEVICE_KEY, deviceId);
}

export async function getPushDeviceId() {
  return SecureStore.getItemAsync(PUSH_DEVICE_KEY);
}

export async function clearPushDeviceId() {
  await SecureStore.deleteItemAsync(PUSH_DEVICE_KEY);
}
