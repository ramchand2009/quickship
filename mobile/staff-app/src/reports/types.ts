import type { Money } from '../orders/types';

export type ProductSalesReportRow = {
  product_id: number | null;
  name: string;
  sku: string | null;
  quantity: number;
  total_sales: Money;
  total_profit: Money;
  profit_complete: boolean;
};

export type ProductSalesReportResponse = {
  data: {
    summary: {
      product_count: number;
      total_quantity: number;
      total_sales: Money;
      total_profit: Money;
      missing_cost_count: number;
    };
    products: ProductSalesReportRow[];
  };
  meta: {
    period: {
      month: string;
      label: string;
      date_from: string;
      date_to: string;
    };
  };
};
