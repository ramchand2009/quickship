import { createContext, useCallback, useContext, useEffect, useMemo, useState, type PropsWithChildren } from 'react';

import * as api from './api';
import {
  clearPushDeviceId,
  clearStoredAuth,
  getInstallationId,
  getPushDeviceId,
  loadStoredAuth,
  saveStoredAuth,
} from './storage';
import type { StoredAuth } from './types';

type AuthContextValue = {
  auth: StoredAuth | null;
  loading: boolean;
  signIn: (username: string, password: string) => Promise<void>;
  signOut: () => Promise<void>;
  chooseTenant: (tenantId: number) => Promise<void>;
  runAuthenticated: <T>(operation: (accessToken: string) => Promise<T>) => Promise<T>;
};

const AuthContext = createContext<AuthContextValue | null>(null);

export function AuthProvider({ children }: PropsWithChildren) {
  const [auth, setAuth] = useState<StoredAuth | null>(null);
  const [loading, setLoading] = useState(true);

  const replaceAuth = useCallback(async (next: StoredAuth | null) => {
    setAuth(next);
    if (next) await saveStoredAuth(next); else await clearStoredAuth();
  }, []);

  const runAuthenticated = useCallback(async <T,>(operation: (accessToken: string) => Promise<T>) => {
    if (!auth) throw new api.ApiError(401, 'authentication_required', 'Please sign in again.');
    try {
      return await operation(auth.tokens.access_token);
    } catch (error) {
      if (!(error instanceof api.ApiError) || error.status !== 401) throw error;
    }

    let refreshed: StoredAuth;
    try {
      const installationId = await getInstallationId();
      const tokens = await api.refresh(auth.tokens.refresh_token, installationId);
      const session = await api.currentSession(tokens.access_token);
      refreshed = { tokens, session };
      await replaceAuth(refreshed);
    } catch (error) {
      if (!(error instanceof api.ApiError) || error.status !== 401) throw error;
      await replaceAuth(null);
      throw new api.ApiError(401, 'session_expired', 'Your session expired. Please sign in again.');
    }
    return operation(refreshed.tokens.access_token);
  }, [auth, replaceAuth]);

  useEffect(() => {
    void (async () => {
      const stored = await loadStoredAuth();
      if (!stored) return setLoading(false);
      try {
        const session = await api.currentSession(stored.tokens.access_token);
        await replaceAuth({ ...stored, session });
      } catch (error) {
        try {
          const installationId = await getInstallationId();
          const tokens = await api.refresh(stored.tokens.refresh_token, installationId);
          const session = await api.currentSession(tokens.access_token);
          await replaceAuth({ tokens, session });
        } catch {
          await replaceAuth(null);
        }
      } finally {
        setLoading(false);
      }
    })();
  }, [replaceAuth]);

  const value = useMemo<AuthContextValue>(() => ({
    auth,
    loading,
    signIn: async (username, password) => {
      const installationId = await getInstallationId();
      await replaceAuth(await api.login(username.trim(), password, installationId));
    },
    chooseTenant: async (tenantId) => {
      if (!auth) return;
      await replaceAuth(await api.selectTenant(auth.tokens.access_token, auth.tokens.refresh_token, tenantId));
    },
    runAuthenticated,
    signOut: async () => {
      if (auth) {
        try {
          const pushDeviceId = await getPushDeviceId();
          if (pushDeviceId) await api.disablePushDevice(auth.tokens.access_token, pushDeviceId);
        } catch { /* Push cleanup must not prevent API logout. */ }
        try {
          const installationId = await getInstallationId();
          await api.logout(auth.tokens.access_token, auth.tokens.refresh_token, installationId);
        } catch { /* Local sign-out must always succeed. */ }
      }
      await clearPushDeviceId();
      await replaceAuth(null);
    },
  }), [auth, loading, replaceAuth, runAuthenticated]);

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth() {
  const value = useContext(AuthContext);
  if (!value) throw new Error('useAuth must be used inside AuthProvider');
  return value;
}
