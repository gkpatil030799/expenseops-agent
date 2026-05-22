export type Transaction = {
  id: number;
  plaid_transaction_id: string;
  merchant_name: string | null;
  name: string;
  amount_cents: number;
  amount: string;
  iso_currency_code: string;
  date: string | null;
  authorized_date: string | null;
  pending: boolean;
  status: string;
  agent_question: string | null;
  splitwise_expense_id: string | null;
  last_error: string | null;
  classification_suggestion: "likely_personal" | "likely_shared" | "unsure" | null;
  classification_reason: string | null;
  created_at: string;
  updated_at: string;
};

export type Friend = {
  id: number;
  first_name: string | null;
  last_name: string | null;
  email: string | null;
  display_name: string;
};
