import type { ApiErrorBody, AuthTokens, DashboardResponse, MobileSession, StoredAuth } from './types';
import type {
  OrderDetailResponse,
  OrderListFilters,
  OrderListResponse,
  OrderMutationResponse,
  OrderStatusUpdate,
} from '../orders/types';
import type { ProductDetailResponse, ProductFilters, ProductListResponse, StockMovementResponse } from '../stock/types';
import type {
  MobileDevice,
  MobileNotification,
  NotificationCategory,
  NotificationListResponse,
  NotificationPreferencesResponse,
} from '../notifications/types';

const API_BASE_URL = (process.env.EXPO_PUBLIC_API_URL || 'http://10.0.2.2:8000/api/v1').replace(/\/$/, '');

export class ApiError extends Error {
  constructor(
    public status: number,
    public code: string,
    message: string,
    public fields: Record<string, string[]> = {},
    public retryable = false,
    public requestId = '',
  ) {
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
    throw new ApiError(
      response.status,
      body.error?.code || 'request_failed',
      body.error?.message || 'Request failed.',
      body.error?.fields || {},
      Boolean(body.error?.retryable),
      body.meta?.request_id || response.headers.get('X-Request-ID') || '',
    );
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

export async function dashboard(accessToken: string): Promise<DashboardResponse> {
  return request<DashboardResponse>('/dashboard', {
    headers: { Authorization: `Bearer ${accessToken}` },
  });
}

export async function orders(accessToken: string, filters: OrderListFilters = {}): Promise<OrderListResponse> {
  const query = Object.entries(filters)
    .filter(([, value]) => value !== undefined && value !== '')
    .map(([key, value]) => `${encodeURIComponent(key)}=${encodeURIComponent(String(value))}`)
    .join('&');
  return request<OrderListResponse>(`/orders${query ? `?${query}` : ''}`, {
    headers: { Authorization: `Bearer ${accessToken}` },
  });
}

export async function orderDetail(accessToken: string, orderId: number): Promise<OrderDetailResponse> {
  return request<OrderDetailResponse>(`/orders/${orderId}`, {
    headers: { Authorization: `Bearer ${accessToken}` },
  });
}

export async function notifications(
  accessToken: string,
  filters: { unread_only?: boolean; cursor?: string; page_size?: number } = {},
): Promise<NotificationListResponse> {
  const query = Object.entries(filters)
    .filter(([, value]) => value !== undefined && value !== '')
    .map(([key, value]) => `${encodeURIComponent(key)}=${encodeURIComponent(String(value))}`)
    .join('&');
  return request<NotificationListResponse>(`/notifications${query ? `?${query}` : ''}`, {
    headers: { Authorization: `Bearer ${accessToken}` },
  });
}

export async function markNotificationRead(
  accessToken: string,
  notificationId: number,
  idempotencyKey: string,
): Promise<{ data: MobileNotification }> {
  return request<{ data: MobileNotification }>(`/notifications/${notificationId}/read`, {
    method: 'POST',
    headers: { Authorization: `Bearer ${accessToken}`, 'Idempotency-Key': idempotencyKey },
    body: '{}',
  });
}

export async function notificationPreferences(accessToken: string): Promise<NotificationPreferencesResponse> {
  return request<NotificationPreferencesResponse>('/notification-preferences', {
    headers: { Authorization: `Bearer ${accessToken}` },
  });
}

export async function updateNotificationPreferences(
  accessToken: string,
  preferences: { category: NotificationCategory; enabled: boolean }[],
  idempotencyKey: string,
): Promise<NotificationPreferencesResponse> {
  return request<NotificationPreferencesResponse>('/notification-preferences', {
    method: 'PATCH',
    headers: { Authorization: `Bearer ${accessToken}`, 'Idempotency-Key': idempotencyKey },
    body: JSON.stringify({ preferences }),
  });
}

export async function registerPushToken(
  accessToken: string,
  values: {
    installation_id: string;
    platform: 'android';
    expo_push_token: string;
    app_version: string;
    device_name?: string;
  },
  idempotencyKey: string,
): Promise<{ data: MobileDevice }> {
  return request<{ data: MobileDevice }>('/devices/push-token', {
    method: 'POST',
    headers: { Authorization: `Bearer ${accessToken}`, 'Idempotency-Key': idempotencyKey },
    body: JSON.stringify(values),
  });
}

export async function disablePushDevice(accessToken: string, deviceId: string): Promise<void> {
  await request<void>(`/devices/${deviceId}`, {
    method: 'DELETE',
    headers: { Authorization: `Bearer ${accessToken}` },
  });
}

export async function updateOrderStatus(
  accessToken: string,
  orderId: number,
  values: OrderStatusUpdate,
  idempotencyKey: string,
): Promise<OrderMutationResponse> {
  return request<OrderMutationResponse>(`/orders/${orderId}/status`, {
    method: 'POST',
    headers: { Authorization: `Bearer ${accessToken}`, 'Idempotency-Key': idempotencyKey },
    body: JSON.stringify(values),
  });
}

export async function markOrderPaymentReceived(
  accessToken: string,
  orderId: number,
  expectedVersion: string,
  idempotencyKey: string,
): Promise<OrderMutationResponse> {
  return request<OrderMutationResponse>(`/orders/${orderId}/payment-received`, {
    method: 'POST',
    headers: { Authorization: `Bearer ${accessToken}`, 'Idempotency-Key': idempotencyKey },
    body: JSON.stringify({ expected_version: expectedVersion, confirmed: true }),
  });
}

export async function products(accessToken: string, filters: ProductFilters = {}): Promise<ProductListResponse> {
  const query = Object.entries(filters)
    .filter(([, value]) => value !== undefined && value !== '')
    .map(([key, value]) => `${encodeURIComponent(key)}=${encodeURIComponent(String(value))}`)
    .join('&');
  return request<ProductListResponse>(`/products${query ? `?${query}` : ''}`, {
    headers: { Authorization: `Bearer ${accessToken}` },
  });
}

export async function productDetail(accessToken: string, productId: number): Promise<ProductDetailResponse> {
  return request<ProductDetailResponse>(`/products/${productId}`, {
    headers: { Authorization: `Bearer ${accessToken}` },
  });
}

export async function stockMovements(accessToken: string, productId: number): Promise<StockMovementResponse> {
  return request<StockMovementResponse>(`/stock/movements?product_id=${productId}`, {
    headers: { Authorization: `Bearer ${accessToken}` },
  });
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
