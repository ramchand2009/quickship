import type { Money, OrderDetail, OrderSummary } from '../orders/types';

export type CustomerSummary = {
  key: string;
  name: string;
  phone: string | null;
  email: string | null;
  address: string | null;
  last_order_at: string | null;
  order_count: number;
  total_spent: Money;
  latest_order_reference: string | null;
};

export type CustomerListResponse = {
  data: CustomerSummary[];
  meta: { count: number };
};

export type CustomerDetailResponse = {
  data: {
    customer: CustomerSummary;
    orders: OrderSummary[];
  };
};

export type CustomerOrderDetailResponse = {
  data: OrderDetail;
};
