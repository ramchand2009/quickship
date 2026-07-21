import type { CodeLabel, Money } from '../orders/types';

export type ProductSummary = {
  id: number;
  name: string;
  sku: string;
  barcode: string | null;
  image_url: string | null;
  category: string | null;
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

export type ProductFilters = { search?: string; stock_state?: string; cursor?: string };
export type ProductListResponse = {
  data: ProductSummary[];
  pagination: { next_cursor: string | null; has_more: boolean };
};
export type ProductDetailResponse = { data: ProductDetail };
export type StockMovementResponse = {
  data: StockMovement[];
  pagination: { next_cursor: string | null; has_more: boolean };
};
