export type CodeLabel = { code: string; label: string };
export type Money = { amount: string; currency: string };

export type OrderSummary = {
  id: number;
  reference: string;
  source: CodeLabel;
  status: CodeLabel;
  payment_state: CodeLabel;
  customer_display_name: string | null;
  item_count: number;
  total: Money;
  order_date: string | null;
  tracking_number: string | null;
  attention_required: boolean;
  version: string;
  updated_at: string;
};

export type OrderCustomer = {
  name: string | null;
  phone: string | null;
  email: string | null;
  delivery_address: string | null;
  fields_masked: string[];
};

export type OrderItem = {
  product_id: number | null;
  name: string;
  sku: string | null;
  quantity: number;
  total: Money;
  image_url: string | null;
};

export type OrderActivity = {
  id: number;
  title: string;
  description: string | null;
  actor_display_name: string | null;
  previous_status: string | null;
  current_status: string | null;
  created_at: string;
};

export type OrderAction = {
  code: 'update_status' | 'mark_payment_received';
  label: string;
  target_status: string | null;
  confirmation_required: boolean;
  reason_required: boolean;
  required_fields: string[];
};

export type OrderDetail = OrderSummary & {
  customer: OrderCustomer;
  items: OrderItem[];
  courier_name: string | null;
  shipping_cost: Money;
  payment_received_at: string | null;
  cancellation_reason: string | null;
  cancellation_note: string | null;
  allowed_actions: OrderAction[];
  activity: OrderActivity[];
};

export type OrderListFilters = {
  search?: string;
  status?: string;
  payment_state?: string;
  date_from?: string;
  date_to?: string;
  cursor?: string;
};

export type OrderListResponse = {
  data: OrderSummary[];
  pagination: { next_cursor: string | null; has_more: boolean };
  meta?: { request_id?: string; server_time?: string };
};

export type OrderDetailResponse = {
  data: OrderDetail;
  meta?: { request_id?: string; server_time?: string };
};

export type OrderStatusUpdate = {
  target_status: string;
  expected_version: string;
  customer_phone?: string;
  courier_name?: string;
  tracking_number?: string;
  shipping_base_amount?: string;
  cancellation_reason?: string;
  cancellation_note?: string;
};

export type MutationEffect = {
  code: 'stock_sync' | 'whatsapp_notification' | 'woocommerce_sync';
  state: 'completed' | 'queued' | 'skipped' | 'warning';
  message: string | null;
};

export type OrderMutationResponse = {
  data: {
    order: OrderDetail;
    effects: MutationEffect[];
    replayed: boolean;
  };
};
