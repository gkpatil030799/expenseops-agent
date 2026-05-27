import type { SandboxSyncedTransaction } from "../types";
import { Card, formatDateTime, StatusPill, traceFromName } from "./sandboxUi";

type Props = {
  transactions: SandboxSyncedTransaction[];
  title?: string;
  description?: string;
};

export function SandboxTransactionsTable({
  transactions,
  title = "Latest review-needed transactions",
  description = "Rows may include current-run results or recent ask_user transactions returned by sync responses.",
}: Props) {
  return (
    <Card className="overflow-hidden">
      <div className="border-b border-slate-200 px-4 py-3">
        <h3 className="text-sm font-semibold text-slate-900">{title}</h3>
        <p className="mt-1 text-xs text-slate-500">{description}</p>
      </div>
      <div className="overflow-x-auto">
        <table className="w-full min-w-[820px] text-left text-sm">
          <thead className="bg-slate-50 text-xs uppercase text-slate-500">
            <tr>
              <th className="px-4 py-3 font-medium">ID</th>
              <th className="px-4 py-3 font-medium">Name</th>
              <th className="px-4 py-3 font-medium">Merchant</th>
              <th className="px-4 py-3 text-right font-medium">Amount</th>
              <th className="px-4 py-3 font-medium">Status</th>
              <th className="px-4 py-3 font-medium">Pending</th>
              <th className="px-4 py-3 font-medium">Trace</th>
              <th className="px-4 py-3 font-medium">Created</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-slate-100">
            {transactions.length ? (
              transactions.map((tx, index) => {
                const trace = traceFromName(tx.name);
                return (
                  <tr
                    key={`${tx.id || index}-${tx.name || "tx"}`}
                    className={trace ? "bg-emerald-50/30" : "bg-white"}
                  >
                    <td className="px-4 py-3 font-mono text-xs text-slate-600">{tx.id || "n/a"}</td>
                    <td className="max-w-[280px] truncate px-4 py-3 font-medium text-slate-950">
                      {tx.name || "n/a"}
                    </td>
                    <td className="px-4 py-3 text-slate-600">{tx.merchant_name || "n/a"}</td>
                    <td className="px-4 py-3 text-right font-mono text-slate-900">
                      {formatAmount(tx.amount_cents)}
                    </td>
                    <td className="px-4 py-3">
                      <StatusPill value={tx.status || "unknown"} label={tx.status || "n/a"} />
                    </td>
                    <td className="px-4 py-3">
                      <StatusPill
                        value={tx.pending ? "warning" : "success"}
                        label={tx.pending ? "pending" : "settled"}
                      />
                    </td>
                    <td className={`px-4 py-3 font-mono text-xs ${trace ? "text-emerald-700" : "text-slate-400"}`}>
                      {trace || "none"}
                    </td>
                    <td className="px-4 py-3 text-slate-600">{formatDateTime(tx.created_at)}</td>
                  </tr>
                );
              })
            ) : (
              <tr>
                <td className="px-4 py-8 text-center text-slate-500" colSpan={8}>
                  No transactions in the latest response.
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
    </Card>
  );
}

function formatAmount(cents?: number) {
  if (typeof cents !== "number") return "n/a";
  return `$${(Math.abs(cents) / 100).toFixed(2)}`;
}
