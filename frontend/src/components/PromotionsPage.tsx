import { useEffect, useMemo, useState } from "react";
import { Bookmark, Check, Copy, ExternalLink, RefreshCw, Search, Sparkles, Tag, X } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { api } from "@/lib/api";
import type { PromotionOffer } from "@/types";

export function PromotionsPage() {
  const [offers, setOffers] = useState<PromotionOffer[]>([]);
  const [categories, setCategories] = useState<string[]>([]);
  const [category, setCategory] = useState("");
  const [search, setSearch] = useState("");
  const [busy, setBusy] = useState(false);
  const [copied, setCopied] = useState<number | null>(null);

  async function load() {
    setBusy(true);
    try {
      const [values, groups] = await Promise.all([api<PromotionOffer[]>("/api/promotions"), api<string[]>("/api/promotions/categories")]);
      setOffers(values); setCategories(groups);
    } finally { setBusy(false); }
  }
  useEffect(() => { void load(); }, []);
  const visible = useMemo(() => offers.filter((offer) => (!category || offer.category === category) && (!search || `${offer.merchant} ${offer.headline}`.toLowerCase().includes(search.toLowerCase()))), [offers, category, search]);
  const grouped = useMemo(
    () =>
      visible.reduce<Record<string, PromotionOffer[]>>((result, offer) => {
        (result[offer.category] ||= []).push(offer);
        return result;
      }, {}),
    [visible],
  );

  async function action(offer: PromotionOffer, kind: "save" | "dismiss" | "not_relevant" | "mute_merchant") {
    if (kind === "save") await api(`/api/promotions/${offer.id}/save`, { method: "POST" });
    else if (kind === "dismiss") await api(`/api/promotions/${offer.id}/dismiss`, { method: "POST" });
    else await api(`/api/promotions/${offer.id}/feedback`, { method: "POST", body: JSON.stringify({ feedback_type: kind, metadata: {} }) });
    await load();
  }

  return <div className="space-y-5">
    <header className="overflow-hidden rounded-xl border border-slate-800 bg-slate-950 p-6 text-white shadow-sm">
      <div className="flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between">
        <div><p className="text-xs font-semibold uppercase tracking-[0.18em] text-indigo-300">Promotion Intelligence</p><h1 className="mt-2 text-3xl font-semibold">Best deals for you</h1><p className="mt-2 max-w-2xl text-sm text-slate-300">A short list of concrete, active offers relevant to what you already buy or may need soon.</p></div>
        <Button variant="secondary" onClick={load} disabled={busy} aria-label="Refresh deals"><RefreshCw className={`h-4 w-4 ${busy ? "animate-spin" : ""}`} />Refresh</Button>
      </div>
    </header>
    <Card><CardContent className="grid gap-3 p-4 sm:grid-cols-[1fr_14rem]">
      <div className="relative"><Search className="absolute left-3 top-3 h-4 w-4 text-slate-400"/><Input className="pl-9" value={search} onChange={(e) => setSearch(e.target.value)} placeholder="Search merchant or offer" aria-label="Search deals"/></div>
      <select className="h-10 rounded-md border border-slate-200 bg-white px-3 text-sm" value={category} onChange={(e) => setCategory(e.target.value)} aria-label="Filter by category"><option value="">All categories</option>{categories.map((v) => <option key={v}>{v}</option>)}</select>
    </CardContent></Card>
    {!busy && !visible.length ? <div className="flex min-h-52 flex-col items-center justify-center rounded-xl border border-dashed bg-white p-8 text-center"><Tag className="h-8 w-8 text-slate-400"/><h2 className="mt-3 font-semibold">No useful active deals yet</h2><p className="mt-1 text-sm text-slate-600">Promotion emails without a concrete or relevant offer stay out of this list.</p></div> : null}
    {Object.entries(grouped).map(([group, values]) => <section key={group} className="space-y-3"><h2 className="text-lg font-semibold text-slate-950">{group}</h2><div className="grid gap-4 lg:grid-cols-2">{values?.map((offer) => <Card key={offer.id} className="transition hover:border-indigo-200 hover:shadow-md"><CardContent className="p-5">
      <div className="flex items-start justify-between gap-4"><div><p className="text-sm font-semibold text-indigo-700">{offer.merchant}</p><h3 className="mt-1 text-xl font-semibold text-slate-950">{offer.headline}</h3></div>{offer.saved ? <Bookmark className="h-5 w-5 fill-indigo-600 text-indigo-600"/> : null}</div>
      {offer.description ? <p className="mt-3 text-sm leading-6 text-slate-700">{offer.description}</p> : null}
      <div className="mt-4 rounded-lg border border-slate-200 bg-slate-50 p-3">
        <p className="text-xs font-semibold uppercase tracking-wide text-slate-600">Deal details</p>
        <div className="mt-2 flex flex-wrap gap-2">
          {offer.percent_off != null ? <span className="rounded-full bg-white px-3 py-1 text-sm font-semibold text-slate-900 ring-1 ring-slate-200">{offer.percent_off}% off</span> : null}
          {offer.amount_off != null ? <span className="rounded-full bg-white px-3 py-1 text-sm font-semibold text-slate-900 ring-1 ring-slate-200">${offer.amount_off.toFixed(2)} off</span> : null}
          {offer.minimum_spend != null ? <span className="rounded-full bg-white px-3 py-1 text-sm text-slate-700 ring-1 ring-slate-200">${offer.minimum_spend.toFixed(2)} minimum</span> : null}
          {offer.offer_type ? <span className="rounded-full bg-white px-3 py-1 text-sm capitalize text-slate-700 ring-1 ring-slate-200">{offer.offer_type.split("_").join(" ")}</span> : null}
        </div>
        {offer.terms_summary ? <p className="mt-3 border-t border-slate-200 pt-3 text-sm leading-6 text-slate-600"><span className="font-medium text-slate-800">Terms:</span> {offer.terms_summary}</p> : null}
      </div>
      {offer.expires_at ? <p className="mt-2 text-sm font-medium text-amber-700">Expires {new Date(offer.expires_at).toLocaleDateString()}</p> : <p className="mt-2 text-sm text-slate-500">Expiry not specified</p>}
      {offer.promo_code ? <button className="mt-3 inline-flex items-center gap-2 rounded-lg bg-slate-100 px-3 py-2 font-mono text-sm hover:bg-slate-200" onClick={async () => { await navigator.clipboard.writeText(offer.promo_code!); setCopied(offer.id); }}><span>Code: {offer.promo_code}</span>{copied === offer.id ? <Check className="h-4 w-4"/> : <Copy className="h-4 w-4"/>}</button> : null}
      {offer.why.length ? <div className="mt-4 rounded-lg bg-indigo-50 p-3"><p className="flex items-center gap-2 text-sm font-semibold text-indigo-950"><Sparkles className="h-4 w-4"/>Why this was selected</p><ul className="mt-1 space-y-1 text-sm text-indigo-900">{offer.why.map((reason) => <li key={reason}>• {reason}</li>)}</ul></div> : null}
      <div className="mt-5 flex flex-wrap gap-2">{offer.destination_url ? <Button asChild><a href={offer.destination_url} target="_blank" rel="noopener noreferrer">Open deal<ExternalLink className="h-4 w-4"/></a></Button> : null}<Button variant="outline" onClick={() => action(offer, "save")}><Bookmark className="h-4 w-4"/>Save</Button><Button variant="ghost" onClick={() => action(offer, "dismiss")}><X className="h-4 w-4"/>Dismiss</Button><Button variant="ghost" onClick={() => action(offer, "not_relevant")}>Not relevant</Button><Button variant="ghost" onClick={() => action(offer, "mute_merchant")}>Mute merchant</Button></div>
    </CardContent></Card>)}</div></section>)}
    <details className="rounded-xl border bg-white p-4"><summary className="cursor-pointer text-sm font-semibold">Advanced sync</summary><p className="mt-2 text-sm text-slate-600">Sync reads only Gmail Promotions messages and never changes your mailbox.</p><Button className="mt-3" variant="outline" onClick={async () => { setBusy(true); try { await api("/api/promotions/sync", { method: "POST", body: JSON.stringify({}) }); await load(); } finally { setBusy(false); } }}>Sync Gmail Promotions</Button></details>
  </div>;
}
