import { createContext, useCallback, useContext, useEffect, useMemo, useState, type PropsWithChildren } from 'react';

import * as api from './api';
import { clearStoredAuth, getInstallationId, loadStoredAuth, saveStoredAuth } from './storage';
import type { StoredAuth } from './types';

type AuthContextValue = {
  auth: StoredAuth | null;
  loading: boolean;
  signIn: (username: string, password: string) => Promise<void>;
  signOut: () => Promise<void>;
  chooseTenant: (tenantId: number) => Promise<void>;
};

const AuthContext = createContext<AuthContextValue | null>(null);

export function AuthProvider({ children }: PropsWithChildren) {
  const [auth, setAuth] = useState<StoredAuth | null>(null);
  const [loading, setLoading] = useState(true);

  const replaceAuth = useCallback(async (next: StoredAuth | null) => {
    setAuth(next);
    if (next) await saveStoredAuth(next); else await clearStoredAuth();
  }, []);

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
    signOut: async () => {
      if (auth) {
        try {
          const installationId = await getInstallationId();
          await api.logout(auth.tokens.access_token, auth.tokens.refresh_token, installationId);
        } catch { /* Local sign-out must always succeed. */ }
      }
      await replaceAuth(null);
    },
  }), [auth, loading, replaceAuth]);

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth() {
  const value = useContext(AuthContext);
  if (!value) throw new Error('useAuth must be used inside AuthProvider');
  return value;
}
