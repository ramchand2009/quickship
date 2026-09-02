export type NotificationCategory = 'new_order' | 'order_attention' | 'status_change' | 'routing_alert' | 'integration_alert';

export type MobileNotification = {
  id: number;
  category: NotificationCategory;
  title: string;
  message: string;
  destination: string | null;
  order_id: number | null;
  is_read: boolean;
  read_at: string | null;
  created_at: string;
};

export type NotificationListResponse = {
  data: MobileNotification[];
  pagination: { next_cursor: string | null; has_more: boolean };
  meta: { unread_count: number; request_id?: string; server_time?: string };
};

export type NotificationPreference = {
  category: NotificationCategory;
  enabled: boolean;
  mandatory: boolean;
};

export type NotificationPreferencesResponse = {
  data: NotificationPreference[];
};

export type MobileDevice = {
  id: string;
  installation_id: string;
  platform: 'android';
  app_version: string;
  enabled: boolean;
  last_seen_at: string;
};
