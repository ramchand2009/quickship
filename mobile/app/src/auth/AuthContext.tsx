import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
  type PropsWithChildren,
} from 'react';

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
  const authRef = useRef<StoredAuth | null>(null);
  const refreshPromiseRef = useRef<Promise<StoredAuth> | null>(null);

  const replaceAuth = useCallback(async (next: StoredAuth | null) => {
    authRef.current = next;
    setAuth(next);
    if (next) await saveStoredAuth(next); else await clearStoredAuth();
  }, []);

  const refreshAuth = useCallback(async (current: StoredAuth) => {
    if (refreshPromiseRef.current) return refreshPromiseRef.current;

    const refreshPromise = (async () => {
      const installationId = await getInstallationId();
      const tokens = await api.refresh(current.tokens.refresh_token, installationId);
      const session = await api.currentSession(tokens.access_token);
      const refreshed = { tokens, session };

      const latest = authRef.current;
      if (latest && latest.tokens.refresh_token !== current.tokens.refresh_token) return latest;
      if (!latest) {
        throw new api.ApiError(401, 'authentication_required', 'Please sign in again.');
      }
      await replaceAuth(refreshed);
      return refreshed;
    })();
    refreshPromiseRef.current = refreshPromise;

    try {
      return await refreshPromise;
    } catch (error) {
      if (error instanceof api.ApiError && error.status === 401) await replaceAuth(null);
      throw error;
    } finally {
      if (refreshPromiseRef.current === refreshPromise) refreshPromiseRef.current = null;
    }
  }, [replaceAuth]);

  const runAuthenticated = useCallback(async <T,>(operation: (accessToken: string) => Promise<T>) => {
    const current = authRef.current;
    if (!current) throw new api.ApiError(401, 'authentication_required', 'Please sign in again.');

    try {
      return await operation(current.tokens.access_token);
    } catch (error) {
      if (!(error instanceof api.ApiError) || error.status !== 401) throw error;
    }

    const latest = authRef.current;
    if (!latest) throw new api.ApiError(401, 'session_expired', 'Your session expired. Please sign in again.');
    if (latest.tokens.access_token !== current.tokens.access_token) {
      return operation(latest.tokens.access_token);
    }

    try {
      const refreshed = await refreshAuth(latest);
      return await operation(refreshed.tokens.access_token);
    } catch (error) {
      if (!(error instanceof api.ApiError) || error.status !== 401) throw error;
      throw new api.ApiError(401, 'session_expired', 'Your session expired. Please sign in again.');
    }
  }, [refreshAuth]);

  useEffect(() => {
    void (async () => {
      try {
        const stored = await loadStoredAuth();
        if (!stored) return;
        authRef.current = stored;
        try {
          const session = await api.currentSession(stored.tokens.access_token);
          await replaceAuth({ ...stored, session });
        } catch (error) {
          if (!(error instanceof api.ApiError) || error.status !== 401) {
            await replaceAuth(stored);
            return;
          }
          try {
            await refreshAuth(stored);
          } catch (refreshError) {
            if (!(refreshError instanceof api.ApiError) || refreshError.status !== 401) {
              await replaceAuth(stored);
            }
          }
        }
      } finally {
        setLoading(false);
      }
    })();
  }, [refreshAuth, replaceAuth]);

  const value = useMemo<AuthContextValue>(() => ({
    auth,
    loading,
    signIn: async (username, password) => {
      const installationId = await getInstallationId();
      await replaceAuth(await api.login(username.trim(), password, installationId));
    },
    chooseTenant: async (tenantId) => {
      const current = authRef.current;
      if (!current) return;
      await replaceAuth(await api.selectTenant(
        current.tokens.access_token,
        current.tokens.refresh_token,
        tenantId,
      ));
    },
    runAuthenticated,
    signOut: async () => {
      const current = authRef.current;
      if (current) {
        try {
          const pushDeviceId = await getPushDeviceId();
          if (pushDeviceId) await api.disablePushDevice(current.tokens.access_token, pushDeviceId);
        } catch { /* Push cleanup must not prevent API logout. */ }
        try {
          const installationId = await getInstallationId();
          await api.logout(current.tokens.access_token, current.tokens.refresh_token, installationId);
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
