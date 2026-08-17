import { BellRing, RefreshCw, Save, ShieldCheck } from "lucide-react";
import { useCallback, useEffect, useState, type ReactNode } from "react";

import { AgentResponseRenderer } from "@/agent/AgentResponseRenderer";
import type { AgentNavigationRequest } from "@/agent/pageContext";
import {
  ATTENTION_CATEGORIES,
  parseAttentionCenter,
  parseAttentionPreference,
  type AttentionCenter,
  type AttentionCategory,
  type AttentionPreference,
} from "@/attention";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { api, apiErrorMessage } from "@/lib/api";

const CATEGORY_LABELS: Record<AttentionCategory, string> = {
  transactions: "Expense reviews",
  receipts: "Receipt reviews",
  integrations: "Integration health",
  replenishment: "Likely-due household items",
  deals: "Expiring relevant deals",
  errands: "Due and high-priority errands",
};

export function AttentionCenterPage({
  onNavigate,
}: {
  onNavigate?: (request: AgentNavigationRequest) => void;
}) {
  const [center, setCenter] = useState<AttentionCenter | null>(null);
  const [draft, setDraft] = useState<AttentionPreference | null>(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");

  const load = useCallback(async (signal?: AbortSignal) => {
    setLoading(true);
    setError("");
    try {
      const raw = await api<unknown>("/api/attention", { signal });
      const parsed = parseAttentionCenter(raw);
      setCenter(parsed);
      setDraft(parsed.preferences);
    } catch (caught) {
      if (signal?.aborted) return;
      setError(apiErrorMessage(caught, "Attention Center could not be loaded."));
    } finally {
      if (!signal?.aborted) setLoading(false);
    }
  }, []);

  useEffect(() => {
    const controller = new AbortController();
    void load(controller.signal);
    return () => controller.abort();
  }, [load]);

  async function savePreferences() {
    if (!draft) return;
    setSaving(true);
    setError("");
    setNotice("");
    try {
      const raw = await api<unknown>("/api/attention/preferences", {
        method: "PATCH",
        body: JSON.stringify(draft),
      });
      parseAttentionPreference(raw);
      await load();
      setNotice("Attention preferences saved.");
    } catch (caught) {
      setError(apiErrorMessage(caught, "Attention preferences could not be saved."));
    } finally {
      setSaving(false);
    }
  }

  function toggleCategory(category: AttentionCategory) {
    if (!draft) return;
    const selected = draft.categories.includes(category);
    if (selected && draft.categories.length === 1) return;
    setDraft({
      ...draft,
      categories: selected
        ? draft.categories.filter((value) => value !== category)
        : [...draft.categories, category],
    });
  }

  if (loading && !center) {
    return <div className="ui-skeleton min-h-64 rounded-2xl" aria-label="Loading Attention Center" />;
  }

  return (
    <div className="min-w-0 space-y-5">
      <Card>
        <CardHeader>
          <div className="flex flex-wrap items-start justify-between gap-3">
            <div>
              <CardTitle className="flex items-center gap-2">
                <BellRing className="size-5 text-indigo-600" aria-hidden="true" />
                Attention Center
              </CardTitle>
              <CardDescription className="mt-1 max-w-2xl">
                A deterministic, read-only view of existing ExpenseOps state. It never posts,
                buys, edits, or completes anything.
              </CardDescription>
            </div>
            <Button variant="outline" onClick={() => void load()} disabled={loading}>
              <RefreshCw className={`size-4 ${loading ? "animate-spin" : ""}`} aria-hidden="true" />
              Refresh
            </Button>
          </div>
        </CardHeader>
        <CardContent>
          {error ? <p role="alert" className="rounded-control bg-rose-50 p-3 text-sm text-rose-800">{error}</p> : null}
          {notice ? <p role="status" className="rounded-control bg-emerald-50 p-3 text-sm text-emerald-800">{notice}</p> : null}
          {center?.response ? (
            <AgentResponseRenderer response={center.response} onNavigate={onNavigate} />
          ) : !error ? (
            <div className="rounded-xl border border-dashed border-slate-300 p-5 text-sm text-slate-600">
              In-app attention is paused. Your settings remain available below.
            </div>
          ) : null}
        </CardContent>
      </Card>

      {draft ? (
        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2">
              <ShieldCheck className="size-5 text-indigo-600" aria-hidden="true" />
              Attention controls
            </CardTitle>
            <CardDescription>
              Choose deterministic categories, quiet hours, cooldowns, and delivery channels.
            </CardDescription>
          </CardHeader>
          <CardContent className="space-y-5">
            <div className="grid gap-3 sm:grid-cols-3">
              <Toggle
                label="Attention enabled"
                checked={draft.enabled}
                onChange={(enabled) => setDraft({ ...draft, enabled })}
              />
              <Toggle
                label="Show in app"
                checked={draft.in_app_enabled}
                onChange={(in_app_enabled) => setDraft({ ...draft, in_app_enabled })}
              />
              <Toggle
                label="Telegram enabled"
                checked={draft.telegram_enabled}
                onChange={(telegram_enabled) => setDraft({ ...draft, telegram_enabled })}
              />
            </div>

            <fieldset>
              <legend className="text-sm font-semibold text-slate-900">Included categories</legend>
              <p className="mt-1 text-xs text-slate-500">Keep at least one category selected.</p>
              <div className="mt-3 grid gap-2 sm:grid-cols-2 lg:grid-cols-3">
                {ATTENTION_CATEGORIES.map((category) => (
                  <label key={category} className="flex min-h-11 items-center gap-3 rounded-control border border-slate-200 px-3 py-2 text-sm text-slate-700">
                    <input
                      type="checkbox"
                      checked={draft.categories.includes(category)}
                      onChange={() => toggleCategory(category)}
                      className="size-4 accent-indigo-600"
                    />
                    {CATEGORY_LABELS[category]}
                  </label>
                ))}
              </div>
            </fieldset>

            <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
              <Field label="Telegram delivery mode">
                <select
                  value={draft.delivery_mode}
                  onChange={(event) => setDraft({ ...draft, delivery_mode: event.target.value as "digest" | "immediate" })}
                  className="h-11 w-full rounded-control border border-slate-300 bg-white px-3 text-sm text-slate-800 sm:h-10"
                >
                  <option value="digest">Digest</option>
                  <option value="immediate">Immediate (prepared, not scheduled)</option>
                </select>
              </Field>
              <Field label="Timezone">
                <Input value={draft.timezone} maxLength={64} onChange={(event) => setDraft({ ...draft, timezone: event.target.value })} />
              </Field>
              <Field label="Maximum Telegram alerts per day">
                <Input type="number" min={1} max={10} value={draft.max_alerts_per_day} onChange={(event) => setDraft({ ...draft, max_alerts_per_day: Number(event.target.value) })} />
              </Field>
              <Field label="Quiet hours start (0–23)">
                <Input type="number" min={0} max={23} value={draft.quiet_start_hour} onChange={(event) => setDraft({ ...draft, quiet_start_hour: Number(event.target.value) })} />
              </Field>
              <Field label="Quiet hours end (0–23)">
                <Input type="number" min={0} max={23} value={draft.quiet_end_hour} onChange={(event) => setDraft({ ...draft, quiet_end_hour: Number(event.target.value) })} />
              </Field>
              <Field label="Telegram cooldown (minutes)">
                <Input type="number" min={15} max={1440} value={draft.cooldown_minutes} onChange={(event) => setDraft({ ...draft, cooldown_minutes: Number(event.target.value) })} />
              </Field>
            </div>

            {draft.delivery_mode === "immediate" ? (
              <p className="rounded-control bg-amber-50 p-3 text-sm text-amber-900">
                Immediate Telegram triggers are prepared but intentionally inactive in this beta.
                In-app attention remains available on demand; no background loop is started.
              </p>
            ) : null}
            <Button onClick={() => void savePreferences()} disabled={saving || draft.categories.length === 0}>
              <Save className="size-4" aria-hidden="true" />
              {saving ? "Saving…" : "Save attention controls"}
            </Button>
          </CardContent>
        </Card>
      ) : null}
    </div>
  );
}

function Toggle({
  label,
  checked,
  onChange,
}: {
  label: string;
  checked: boolean;
  onChange: (checked: boolean) => void;
}) {
  return (
    <label className="flex min-h-11 items-center gap-3 rounded-control border border-slate-200 px-3 py-2 text-sm font-medium text-slate-800">
      <input
        type="checkbox"
        checked={checked}
        onChange={(event) => onChange(event.target.checked)}
        className="size-4 accent-indigo-600"
      />
      {label}
    </label>
  );
}

function Field({ label, children }: { label: string; children: ReactNode }) {
  return (
    <label className="space-y-1.5 text-sm font-medium text-slate-800">
      <span>{label}</span>
      {children}
    </label>
  );
}
