import type { Money, OrderDetail, OrderSummary } from '../orders/types';

export type ManualOrderItemInput = {
  product_id: number;
  quantity: number;
};

export type CustomerSummary = {
  key: string;
  name: string;
  phone: string | null;
  email: string | null;
  address: string | null;
  shipping_address: CustomerAddressInput | null;
  last_order_at: string | null;
  order_count: number;
  total_spent: Money;
  latest_order_reference: string | null;
  source: 'saved' | 'orders';
};

export type CustomerAddressInput = {
  name: string;
  phone: string;
  email?: string;
  address_1: string;
  address_2?: string;
  city: string;
  state: string;
  pincode: string;
  country?: string;
};

export type ShippingLabelSender = {
  name: string;
  phone: string | null;
  address: string | null;
};

export type CustomerListResponse = {
  data: CustomerSummary[];
  meta: { count: number };
};

export type CustomerDetailResponse = {
  data: {
    customer: CustomerSummary;
    orders: OrderSummary[];
    sender: ShippingLabelSender;
  };
};

export type CustomerCreateResponse = {
  data: {
    customer: CustomerSummary;
    sender: ShippingLabelSender;
  };
};

export type CustomerOrderDetailResponse = {
  data: OrderDetail;
};

export type ManualOrderCreateInput = {
  customer_key?: string;
  customer?: CustomerAddressInput;
  items: ManualOrderItemInput[];
  note?: string;
};

export type ManualOrderCreateResponse = {
  data: {
    order: OrderDetail;
    customer: CustomerSummary;
    whatsapp: {
      phone: string;
      message: string;
      confirmation_url: string;
    };
    effects: { code: string; state: string; message: string | null }[];
    replayed: boolean;
  };
};
