export type Expense = { id: number; item_name: string; quantity: number; unit_price: string; total_amount: string; remark: string; created_by: string; created_at: string };
export type ExpenseListResponse = { data: { expenses: Expense[]; total: string }; meta: { period: { month: string; label: string } } };
export type ExpenseCreate = { item_name: string; quantity: number; unit_price: string; remark?: string };
