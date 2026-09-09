import type { CodeLabel, Money } from '../orders/types';

export type ProductSummary = {
  id: number;
  name: string;
  sku: string;
  barcode: string | null;
  image_url: string | null;
  category: string | null;
  prices: { actual: Money | null; regular: Money | null; sale: Money | null };
  stock_quantity: number;
  reorder_level: number;
  stock_state: 'in_stock' | 'low_stock' | 'out_of_stock';
  route_ready: boolean;
  is_active: boolean;
  updated_at: string;
};

export type ProductDetail = ProductSummary & {
  description: string | null;
  prices: { actual: Money | null; regular: Money | null; sale: Money | null };
  routing: {
    ready: boolean;
    woocommerce_product_id: string | null;
    woocommerce_variation_id: string | null;
  };
  can_adjust_stock: boolean;
  can_edit_product: boolean;
};

export type StockMovement = {
  id: number;
  product_id: number;
  order_id: number | null;
  movement_type: CodeLabel;
  quantity_delta: number;
  quantity_after: number;
  note: string | null;
  actor_display_name: string | null;
  created_at: string;
};

export type ProductFilters = { search?: string; stock_state?: string; category?: string; cursor?: string };
export type ProductListResponse = {
  data: ProductSummary[];
  pagination: { next_cursor: string | null; has_more: boolean };
  meta?: { total_count?: number; attention_count?: number; categories?: string[] };
};
export type ProductSyncResponse = {
  data: {
    synced: boolean;
    message: string;
    summary: {
      products_seen: number;
      variations_seen: number;
      created: number;
      updated: number;
      unchanged: number;
      skipped: number;
    };
  };
};
export type ProductDetailResponse = { data: ProductDetail };
export type StockMovementResponse = {
  data: StockMovement[];
  pagination: { next_cursor: string | null; has_more: boolean };
};

export type StockQuantityUpdate = {
  expected_quantity: number;
  target_quantity: number;
  note?: string;
};

export type StockQuantityMutationResponse = {
  data: {
    product: ProductDetail;
    movement: StockMovement | null;
    replayed: boolean;
    effects: { code: string; state: string; message: string | null }[];
  };
};

export type ProductUpdate = {
  expected_updated_at: string;
  name: string;
  sku: string;
  barcode?: string | null;
  category?: string;
  description?: string;
  actual_price?: string | null;
  regular_price?: string | null;
  sale_price?: string | null;
  reorder_level: number;
  is_active: boolean;
};

export type ProductCreate = {
  name: string;
  sku: string;
  barcode?: string | null;
  category?: string;
  description?: string;
  actual_price?: string | null;
  regular_price?: string | null;
  sale_price?: string | null;
  stock_quantity: number;
  reorder_level: number;
  is_active: boolean;
};
