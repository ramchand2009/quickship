export type AuthTokens = {
  access_token: string;
  access_expires_at: string;
  refresh_token: string;
  refresh_expires_at: string;
};

export type TenantMembership = {
  tenant_id: number;
  tenant_name: string;
  tenant_slug: string;
  role: string;
  role_label: string;
};

export type MobileSession = {
  user: { id: number; username: string; display_name: string; email: string | null };
  active_tenant: TenantMembership | null;
  available_tenants: TenantMembership[];
  permissions: string[];
};

export type StoredAuth = { tokens: AuthTokens; session: MobileSession };

export type ApiErrorBody = {
  error?: { code?: string; message?: string; fields?: Record<string, string[]>; retryable?: boolean };
};
