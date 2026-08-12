import { useEffect, useMemo, useState } from "react";
import { Bookmark, Check, Copy, ExternalLink, RefreshCw, Search, Sparkles, Tag, X } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { PageHeader } from "@/components/ui/page-header";
import { api } from "@/lib/api";
import type { PromotionOffer } from "@/types";

type DealView = "recommended" | "saved" | "expiring" | "all";
type RemovedDeal = { offer: PromotionOffer; reason: "dismiss" | "not_relevant" };

export function PromotionsPage() {
  const [offers, setOffers] = useState<PromotionOffer[]>([]);
  const [categories, setCategories] = useState<string[]>([]);
  const [category, setCategory] = useState("");
  const [search, setSearch] = useState("");
  const [view, setView] = useState<DealView>("recommended");
  const [busy, setBusy] = useState(false);
  const [busyOfferId, setBusyOfferId] = useState<number | null>(null);
  const [copied, setCopied] = useState<number | null>(null);
  const [removed, setRemoved] = useState<RemovedDeal | null>(null);
  const [gmailConnected, setGmailConnected] = useState<boolean | null>(null);

  async function load() {
    setBusy(true);
    try {
      const [values, groups] = await Promise.all([
        api<PromotionOffer[]>("/api/promotions?limit=100"),
        api<string[]>("/api/promotions/categories"),
      ]);
      setOffers(values);
      setCategories(groups);
    } finally {
      setBusy(false);
    }
  }

  useEffect(() => { void load(); }, []);
  useEffect(() => {
    void api<{ gmail: { connected: boolean } }>("/api/integrations")
      .then((value) => setGmailConnected(value.gmail.connected))
      .catch(() => setGmailConnected(null));
  }, []);

  const expiringIds = useMemo(() => {
    const cutoff = Date.now() + 7 * 86_400_000;
    return new Set(offers.filter((offer) => offer.expires_at && new Date(offer.expires_at).getTime() <= cutoff).map((offer) => offer.id));
  }, [offers]);

  const searched = useMemo(() => offers.filter((offer) =>
    (!category || offer.category === category)
    && (!search || `${offer.merchant} ${offer.headline} ${offer.description || ""}`.toLowerCase().includes(search.toLowerCase())),
  ), [offers, category, search]);

  const visible = useMemo(() => {
    if (view === "saved") return searched.filter((offer) => offer.saved);
    if (view === "expiring") return searched.filter((offer) => expiringIds.has(offer.id));
    if (view === "recommended") return searched.slice(0, 10);
    return searched;
  }, [searched, view, expiringIds]);

  async function action(offer: PromotionOffer, kind: "save" | "dismiss" | "not_relevant" | "mute_merchant") {
    setBusyOfferId(offer.id);
    try {
      if (kind === "save") {
        const updated = await api<PromotionOffer>(`/api/promotions/${offer.id}/save`, { method: "POST" });
        setOffers((current) => current.map((item) => item.id === offer.id ? updated : item));
        return;
      }
      if (kind === "dismiss") await api(`/api/promotions/${offer.id}/dismiss`, { method: "POST" });
      else await api(`/api/promotions/${offer.id}/feedback`, { method: "POST", body: JSON.stringify({ feedback_type: kind, metadata: {} }) });
      setOffers((current) => kind === "mute_merchant" ? current.filter((item) => item.merchant !== offer.merchant) : current.filter((item) => item.id !== offer.id));
      setRemoved(kind === "dismiss" || kind === "not_relevant" ? { offer, reason: kind } : null);
    } finally {
      setBusyOfferId(null);
    }
  }

  async function undoRemoval() {
    if (!removed) return;
    const restored = await api<PromotionOffer>(`/api/promotions/${removed.offer.id}/restore`, { method: "POST" });
    setOffers((current) => [restored, ...current.filter((offer) => offer.id !== restored.id)].sort((a, b) => Number(b.saved) - Number(a.saved) || b.score - a.score));
    setRemoved(null);
  }

  const featured = view === "all" ? visible.slice(0, 6) : visible;
  const overflow = view === "all" ? visible.slice(6) : [];

  return <div className="space-y-5">
    <PageHeader
      eyebrow={<span className="inline-flex items-center gap-2"><Tag className="h-4 w-4" aria-hidden="true" />Deals</span>}
      title="Deals worth your attention"
      description="Start with the strongest matches. Browse the full feed only when you want to."
      actions={<Button className="border-white/15 bg-white/10 text-white hover:bg-white/15 hover:text-white" variant="outline" onClick={load} disabled={busy} aria-label="Refresh deals"><RefreshCw className={`h-4 w-4 ${busy ? "animate-spin" : ""}`} />Refresh</Button>}
    />

    {removed ? <div className="flex flex-wrap items-center justify-between gap-3 rounded-xl border border-slate-200 bg-white px-4 py-3 text-sm text-slate-700 shadow-sm"><span>{removed.offer.merchant} was removed from your active deals.</span><Button size="sm" variant="outline" onClick={undoRemoval}>Undo</Button></div> : null}
    {gmailConnected === false ? <div className="flex flex-wrap items-center justify-between gap-3 rounded-xl border border-indigo-200 bg-indigo-50 px-4 py-3 text-sm text-indigo-950"><span><strong>Gmail is not connected.</strong> Connect it to import your promotion emails automatically.</span><Button size="sm" onClick={() => window.location.assign("/?workspace=settings")}>Connect Gmail</Button></div> : null}

    <Card><CardContent className="space-y-4 p-4">
      <nav className="flex gap-1 overflow-x-auto" aria-label="Deal views">
        <DealViewButton active={view === "recommended"} onClick={() => setView("recommended")} label="Best for you" count={Math.min(offers.length, 10)} />
        <DealViewButton active={view === "saved"} onClick={() => setView("saved")} label="Saved" count={offers.filter((offer) => offer.saved).length} />
        <DealViewButton active={view === "expiring"} onClick={() => setView("expiring")} label="Expiring soon" count={expiringIds.size} />
        <DealViewButton active={view === "all"} onClick={() => setView("all")} label="All deals" count={offers.length} />
      </nav>
      <div className="grid gap-3 sm:grid-cols-[1fr_14rem]">
        <div className="relative"><Search className="absolute left-3 top-3 h-4 w-4 text-slate-400"/><Input className="pl-9" value={search} onChange={(event) => setSearch(event.target.value)} placeholder="Search merchant or offer" aria-label="Search deals"/></div>
        <select className="h-10 rounded-md border border-slate-200 bg-white px-3 text-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-indigo-500" value={category} onChange={(event) => setCategory(event.target.value)} aria-label="Filter by category"><option value="">All categories</option>{categories.map((value) => <option key={value}>{value}</option>)}</select>
      </div>
    </CardContent></Card>

    {!busy && !visible.length ? <div className="flex min-h-52 flex-col items-center justify-center rounded-xl border border-dashed bg-white p-8 text-center"><Tag className="h-8 w-8 text-slate-400"/><h2 className="mt-3 font-semibold">No deals in this view</h2><p className="mt-1 text-sm text-slate-600">Try another view or clear the current filters.</p></div> : null}

    {featured.length ? <section className="space-y-3"><div className="flex items-end justify-between gap-3"><div><h2 className="text-lg font-semibold text-slate-950">{view === "recommended" ? "Best matches" : view === "saved" ? "Saved for later" : view === "expiring" ? "Ending within seven days" : "Top-ranked deals"}</h2><p className="mt-1 text-xs text-slate-600">Ranked using relevance, offer quality, expiry, and your feedback.</p></div>{view === "recommended" && offers.length > 10 ? <Button variant="ghost" size="sm" onClick={() => setView("all")}>See all {offers.length}</Button> : null}</div><div className="grid gap-4 lg:grid-cols-2">{featured.map((offer) => <DealCard key={offer.id} offer={offer} busy={busyOfferId === offer.id} copied={copied === offer.id} onCopy={async () => { if (!offer.promo_code) return; await navigator.clipboard.writeText(offer.promo_code); setCopied(offer.id); }} onAction={(kind) => action(offer, kind)} />)}</div></section> : null}

    {overflow.length ? <section className="space-y-3"><div><h2 className="text-lg font-semibold text-slate-950">More active deals</h2><p className="mt-1 text-xs text-slate-600">Compact rows keep lower-ranked offers available without dominating the page.</p></div><div className="divide-y divide-slate-200 overflow-hidden rounded-xl border border-slate-200 bg-white">{overflow.map((offer) => <CompactDealRow key={offer.id} offer={offer} busy={busyOfferId === offer.id} onOpen={() => offer.destination_url && window.open(offer.destination_url, "_blank", "noopener,noreferrer")} onAction={(kind) => action(offer, kind)} />)}</div></section> : null}

    <details className="rounded-xl border bg-white p-4"><summary className="cursor-pointer text-sm font-semibold focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-indigo-500">Advanced sync</summary><p className="mt-2 text-sm text-slate-600">Deals import automatically on the Railway schedule. Manual sync remains available for troubleshooting or immediate refresh.</p><Button className="mt-3" variant="outline" disabled={gmailConnected === false} onClick={async () => { setBusy(true); try { await api("/api/promotions/sync", { method: "POST", body: JSON.stringify({}) }); await load(); } finally { setBusy(false); } }}>Sync Gmail Promotions now</Button></details>
  </div>;
}

function DealViewButton({ active, onClick, label, count }: { active: boolean; onClick: () => void; label: string; count: number }) {
  return <button type="button" onClick={onClick} aria-current={active ? "page" : undefined} className={`whitespace-nowrap rounded-lg px-3 py-2 text-sm font-medium transition focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-indigo-500 ${active ? "bg-indigo-600 text-white" : "text-slate-600 hover:bg-slate-50 hover:text-slate-950"}`}>{label}<span className={`ml-2 rounded-full px-1.5 py-0.5 text-xs ${active ? "bg-white/20" : "bg-slate-100"}`}>{count}</span></button>;
}

function DealCard({ offer, busy, copied, onCopy, onAction }: { offer: PromotionOffer; busy: boolean; copied: boolean; onCopy: () => void; onAction: (kind: "save" | "dismiss" | "not_relevant" | "mute_merchant") => void }) {
  return <Card className="transition hover:border-indigo-200 hover:shadow-md"><CardContent className="p-5"><div className="flex items-start justify-between gap-4"><div><p className="text-sm font-semibold text-indigo-700">{offer.merchant}</p><h3 className="mt-1 text-xl font-semibold text-slate-950">{offer.headline}</h3></div>{offer.saved ? <Bookmark className="h-5 w-5 fill-indigo-600 text-indigo-600"/> : null}</div>{offer.description ? <p className="mt-3 text-sm leading-6 text-slate-700">{offer.description}</p> : null}<DealDetails offer={offer} />{offer.expires_at ? <p className="mt-2 text-sm font-medium text-amber-700">Expires {new Date(offer.expires_at).toLocaleDateString()}</p> : <p className="mt-2 text-sm text-slate-500">Expiry not specified</p>}{offer.promo_code ? <button className="mt-3 inline-flex items-center gap-2 rounded-lg bg-slate-100 px-3 py-2 font-mono text-sm hover:bg-slate-200 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-indigo-500" onClick={onCopy}><span>Code: {offer.promo_code}</span>{copied ? <Check className="h-4 w-4"/> : <Copy className="h-4 w-4"/>}</button> : null}{offer.why.length ? <div className="mt-4 rounded-lg bg-indigo-50 p-3"><p className="flex items-center gap-2 text-sm font-semibold text-indigo-950"><Sparkles className="h-4 w-4"/>Why this was selected</p><ul className="mt-1 space-y-1 text-sm text-indigo-900">{offer.why.map((reason) => <li key={reason}>• {reason}</li>)}</ul></div> : null}<DealActions offer={offer} busy={busy} onAction={onAction} /></CardContent></Card>;
}

function DealDetails({ offer }: { offer: PromotionOffer }) {
  if (offer.percent_off == null && offer.amount_off == null && offer.minimum_spend == null && !offer.offer_type && !offer.terms_summary) return null;
  return <div className="mt-4 rounded-lg border border-slate-200 bg-slate-50 p-3"><p className="text-xs font-semibold uppercase tracking-wide text-slate-600">Deal details</p><div className="mt-2 flex flex-wrap gap-2">{offer.percent_off != null ? <Badge variant="secondary">{offer.percent_off}% off</Badge> : null}{offer.amount_off != null ? <Badge variant="secondary">${offer.amount_off.toFixed(2)} off</Badge> : null}{offer.minimum_spend != null ? <Badge variant="secondary">${offer.minimum_spend.toFixed(2)} minimum</Badge> : null}{offer.offer_type ? <Badge variant="secondary" className="capitalize">{offer.offer_type.split("_").join(" ")}</Badge> : null}</div>{offer.terms_summary ? <p className="mt-3 border-t border-slate-200 pt-3 text-sm leading-6 text-slate-600"><span className="font-medium text-slate-800">Terms:</span> {offer.terms_summary}</p> : null}</div>;
}

function DealActions({ offer, busy, onAction }: { offer: PromotionOffer; busy: boolean; onAction: (kind: "save" | "dismiss" | "not_relevant" | "mute_merchant") => void }) {
  return <div className="mt-5 flex flex-wrap gap-2">{offer.destination_url ? <Button asChild><a href={offer.destination_url} target="_blank" rel="noopener noreferrer">Open deal<ExternalLink className="h-4 w-4"/></a></Button> : null}<Button variant="outline" onClick={() => onAction("save")} disabled={busy || offer.saved}><Bookmark className="h-4 w-4"/>{offer.saved ? "Saved" : "Save"}</Button><Button variant="ghost" onClick={() => onAction("dismiss")} disabled={busy}><X className="h-4 w-4"/>Dismiss</Button><Button variant="ghost" onClick={() => onAction("not_relevant")} disabled={busy}>Not relevant</Button><Button variant="ghost" onClick={() => onAction("mute_merchant")} disabled={busy}>Mute merchant</Button></div>;
}

function CompactDealRow({ offer, busy, onOpen, onAction }: { offer: PromotionOffer; busy: boolean; onOpen: () => void; onAction: (kind: "save" | "dismiss") => void }) {
  return <div className="flex flex-col gap-3 p-4 sm:flex-row sm:items-center sm:justify-between"><div className="min-w-0"><div className="flex flex-wrap items-center gap-2"><p className="text-sm font-semibold text-indigo-700">{offer.merchant}</p>{offer.saved ? <Badge variant="secondary">Saved</Badge> : null}</div><p className="mt-1 truncate font-semibold text-slate-950">{offer.headline}</p><p className="mt-1 text-xs text-slate-600">{offer.expires_at ? `Expires ${new Date(offer.expires_at).toLocaleDateString()}` : "Expiry not specified"}{offer.promo_code ? ` · Code ${offer.promo_code}` : ""}</p></div><div className="flex shrink-0 gap-2">{offer.destination_url ? <Button size="sm" variant="outline" onClick={onOpen}>Open</Button> : null}<Button size="sm" variant="ghost" onClick={() => onAction("save")} disabled={busy || offer.saved}>{offer.saved ? "Saved" : "Save"}</Button><Button size="sm" variant="ghost" onClick={() => onAction("dismiss")} disabled={busy}>Dismiss</Button></div></div>;
}
