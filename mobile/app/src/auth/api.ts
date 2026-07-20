import type { ApiErrorBody, AuthTokens, MobileSession, StoredAuth } from './types';

const API_BASE_URL = (process.env.EXPO_PUBLIC_API_URL || 'http://10.0.2.2:8000/api/v1').replace(/\/$/, '');

export class ApiError extends Error {
  constructor(public status: number, public code: string, message: string) {
    super(message);
  }
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  let response: Response;
  try {
    response = await fetch(`${API_BASE_URL}${path}`, {
      ...init,
      headers: { Accept: 'application/json', 'Content-Type': 'application/json', ...init.headers },
    });
  } catch {
    throw new ApiError(0, 'network_error', 'Cannot reach the server. Check your connection and API address.');
  }
  if (!response.ok) {
    const body = (await response.json().catch(() => ({}))) as ApiErrorBody;
    throw new ApiError(response.status, body.error?.code || 'request_failed', body.error?.message || 'Request failed.');
  }
  if (response.status === 204) return undefined as T;
  return (await response.json()) as T;
}

export async function login(username: string, password: string, installationId: string): Promise<StoredAuth> {
  const body = await request<{ data: StoredAuth }>('/auth/login', {
    method: 'POST',
    body: JSON.stringify({ username, password, installation_id: installationId, platform: 'android', app_version: '1.0.0' }),
  });
  return body.data;
}

export async function refresh(refreshToken: string, installationId: string): Promise<AuthTokens> {
  const body = await request<{ data: AuthTokens }>('/auth/refresh', {
    method: 'POST', body: JSON.stringify({ refresh_token: refreshToken, installation_id: installationId }),
  });
  return body.data;
}

export async function currentSession(accessToken: string): Promise<MobileSession> {
  const body = await request<{ data: MobileSession }>('/auth/me', {
    headers: { Authorization: `Bearer ${accessToken}` },
  });
  return body.data;
}

export async function selectTenant(accessToken: string, refreshToken: string, tenantId: number): Promise<StoredAuth> {
  const body = await request<{ data: StoredAuth }>('/auth/select-tenant', {
    method: 'POST', headers: { Authorization: `Bearer ${accessToken}` },
    body: JSON.stringify({ tenant_id: tenantId, refresh_token: refreshToken }),
  });
  return body.data;
}

export async function logout(accessToken: string, refreshToken: string, installationId: string) {
  await request<void>('/auth/logout', {
    method: 'POST', headers: { Authorization: `Bearer ${accessToken}` },
    body: JSON.stringify({ refresh_token: refreshToken, installation_id: installationId }),
  });
}
