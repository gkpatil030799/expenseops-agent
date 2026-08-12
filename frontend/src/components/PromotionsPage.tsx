import * as Dialog from "@radix-ui/react-dialog";
import { useEffect, useMemo, useState } from "react";
import {
  Bookmark,
  Check,
  Copy,
  ExternalLink,
  RefreshCw,
  Search,
  Sparkles,
  Tag,
  X,
} from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { MerchantAvatar } from "@/components/ui/merchant-avatar";
import { OverflowMenu } from "@/components/ui/overflow-menu";
import { PageHeader } from "@/components/ui/page-header";
import { StatusMessage } from "@/components/ui/status-message";
import { api } from "@/lib/api";
import type { PromotionOffer } from "@/types";

type DealView = "recommended" | "saved" | "expiring" | "all";
type RemovedDeal = { offer: PromotionOffer; reason: "dismiss" | "not_relevant" };
type OfferAction = "save" | "dismiss" | "not_relevant" | "mute_merchant";

export function PromotionsPage() {
  const [offers, setOffers] = useState<PromotionOffer[]>([]);
  const [categories, setCategories] = useState<string[]>([]);
  const [category, setCategory] = useState("");
  const [search, setSearch] = useState("");
  const [view, setView] = useState<DealView>("recommended");
  const [busy, setBusy] = useState(false);
  const [hasLoaded, setHasLoaded] = useState(false);
  const [loadError, setLoadError] = useState(false);
  const [busyOfferId, setBusyOfferId] = useState<number | null>(null);
  const [copied, setCopied] = useState<number | null>(null);
  const [removed, setRemoved] = useState<RemovedDeal | null>(null);
  const [gmailConnected, setGmailConnected] = useState<boolean | null>(null);

  async function load() {
    setBusy(true);
    setLoadError(false);
    try {
      const [values, groups] = await Promise.all([
        api<PromotionOffer[]>("/api/promotions?limit=100"),
        api<string[]>("/api/promotions/categories"),
      ]);
      setOffers(values);
      setCategories(groups);
    } catch {
      setLoadError(true);
    } finally {
      setHasLoaded(true);
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
    return new Set(offers.filter((offer) => {
      if (!offer.expires_at) return false;
      const expires = new Date(offer.expires_at).getTime();
      return Number.isFinite(expires) && expires <= cutoff;
    }).map((offer) => offer.id));
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

  async function action(offer: PromotionOffer, kind: OfferAction) {
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

  async function syncPromotions() {
    setBusy(true);
    try {
      await api("/api/promotions/sync", { method: "POST", body: JSON.stringify({}) });
      await load();
    } finally {
      setBusy(false);
    }
  }

  function clearFilters() {
    setSearch("");
    setCategory("");
    setView("recommended");
  }

  const featured = view === "all" ? visible.slice(0, 6) : visible;
  const overflow = view === "all" ? visible.slice(6) : [];
  const filteredEmpty = offers.length > 0 && visible.length === 0;

  return <div className="space-y-5">
    <PageHeader
      eyebrow={<span className="inline-flex items-center gap-2"><Tag className="h-4 w-4" aria-hidden="true" />Deals</span>}
      title="Deals worth your attention"
      description="See the strongest offers first, understand where each link goes, and teach ExpenseOps what matters."
      actions={<Button className="border-white/15 bg-white/10 text-white hover:bg-white/15 hover:text-white" variant="outline" onClick={load} disabled={busy} aria-label="Refresh deals"><RefreshCw className={`h-4 w-4 ${busy ? "animate-spin" : ""}`} />Refresh</Button>}
    />

    {removed ? <StatusMessage title="Deal removed" actions={<Button size="sm" variant="outline" onClick={undoRemoval}>Undo</Button>}>{removed.offer.merchant} was removed from your active deals.</StatusMessage> : null}
    {loadError ? <StatusMessage tone="error" title="Deals could not be loaded" actions={<Button size="sm" variant="outline" onClick={load}>Try again</Button>}>Your existing deals have not been replaced with an empty state.</StatusMessage> : null}

    {hasLoaded && !loadError && offers.length === 0 ? <DealsEmptyState gmailConnected={gmailConnected} busy={busy} onSync={syncPromotions} /> : <>
      <Card variant="secondary"><CardContent className="space-y-4 p-4 sm:p-4">
        <nav className="flex gap-1 overflow-x-auto pb-1" aria-label="Deal views">
          <DealViewButton active={view === "recommended"} onClick={() => setView("recommended")} label="Best for you" count={Math.min(offers.length, 10)} />
          <DealViewButton active={view === "saved"} onClick={() => setView("saved")} label="Saved" count={offers.filter((offer) => offer.saved).length} />
          <DealViewButton active={view === "expiring"} onClick={() => setView("expiring")} label="Expiring soon" count={expiringIds.size} />
          <DealViewButton active={view === "all"} onClick={() => setView("all")} label="All deals" count={offers.length} />
        </nav>
        <div className="grid gap-3 sm:grid-cols-[1fr_14rem]">
          <div className="relative"><Search className="absolute left-3 top-3 h-4 w-4 text-slate-500" aria-hidden="true"/><Input className="pl-9" value={search} onChange={(event) => setSearch(event.target.value)} placeholder="Search merchant or offer" aria-label="Search deals"/></div>
          <select className="h-11 rounded-control border border-slate-200 bg-white px-3 text-sm text-slate-800 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-indigo-500 sm:h-10" value={category} onChange={(event) => setCategory(event.target.value)} aria-label="Filter by category"><option value="">All categories</option>{categories.map((value) => <option key={value}>{value}</option>)}</select>
        </div>
      </CardContent></Card>

      {!hasLoaded && busy ? <DealsSkeleton /> : null}
      {hasLoaded && filteredEmpty ? <FilteredEmptyState view={view} filtered={Boolean(search || category)} onClear={clearFilters} /> : null}

      {featured.length ? <section className="space-y-3" aria-labelledby="deal-section-title"><div className="flex items-end justify-between gap-3"><div><h2 id="deal-section-title" className="text-lg font-semibold text-slate-950">{view === "recommended" ? "Best matches" : view === "saved" ? "Saved for later" : view === "expiring" ? "Ending within seven days" : "Top-ranked deals"}</h2><p className="mt-1 text-sm text-slate-600">Ranked using offer value, relevance, expiry, and your feedback.</p></div>{view === "recommended" && offers.length > 10 ? <Button variant="ghost" size="sm" onClick={() => setView("all")}>See all {offers.length}</Button> : null}</div><div className="grid gap-4 lg:grid-cols-2">{featured.map((offer) => <DealCard key={offer.id} offer={offer} busy={busyOfferId === offer.id} copied={copied === offer.id} onCopy={async () => { if (!offer.promo_code) return; await navigator.clipboard.writeText(offer.promo_code); setCopied(offer.id); window.setTimeout(() => setCopied(null), 2_000); }} onAction={(kind) => action(offer, kind)} />)}</div></section> : null}

      {overflow.length ? <section className="space-y-3"><div><h2 className="text-lg font-semibold text-slate-950">More active deals</h2><p className="mt-1 text-sm text-slate-600">Lower-ranked offers stay available without competing with the strongest matches.</p></div><div className="divide-y divide-slate-200 overflow-hidden rounded-card border border-slate-200 bg-white shadow-card">{overflow.map((offer) => <CompactDealRow key={offer.id} offer={offer} busy={busyOfferId === offer.id} onAction={(kind) => action(offer, kind)} />)}</div></section> : null}
    </>}

    <details className="rounded-card border border-ui-border bg-white p-4 shadow-card"><summary className="min-h-11 cursor-pointer py-2 text-sm font-semibold focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-indigo-500">Import and sync details</summary><p className="mt-2 max-w-2xl text-sm leading-6 text-slate-600">Deals import automatically on the configured schedule. Manual sync is available when you want to check Gmail immediately.</p><Button className="mt-3" variant="outline" disabled={gmailConnected === false || busy} onClick={syncPromotions}>Sync Gmail promotions</Button></details>
  </div>;
}

function DealsEmptyState({ gmailConnected, busy, onSync }: { gmailConnected: boolean | null; busy: boolean; onSync: () => void }) {
  const disconnected = gmailConnected === false;
  return <Card><CardContent className="flex min-h-64 flex-col items-center justify-center p-8 text-center"><div className="flex h-12 w-12 items-center justify-center rounded-full bg-indigo-50 text-indigo-700"><Tag className="h-6 w-6" aria-hidden="true" /></div><h2 className="mt-4 text-lg font-semibold text-slate-950">{disconnected ? "Connect Gmail to find your deals" : "No active deals need your attention"}</h2><p className="mt-2 max-w-md text-sm leading-6 text-slate-600">{disconnected ? "ExpenseOps can privately scan promotion emails and surface concrete offers here. You stay in control of the connection." : "New qualifying promotions will appear here after the next Gmail import. There is nothing to review right now."}</p><div className="mt-5">{disconnected ? <Button onClick={() => window.location.assign("/?workspace=settings")}>Connect Gmail</Button> : gmailConnected ? <Button variant="outline" onClick={onSync} disabled={busy}><RefreshCw className={`h-4 w-4 ${busy ? "animate-spin" : ""}`} aria-hidden="true"/>Check Gmail now</Button> : null}</div></CardContent></Card>;
}

function FilteredEmptyState({ view, filtered, onClear }: { view: DealView; filtered: boolean; onClear: () => void }) {
  const title = filtered ? "No deals match these filters" : view === "saved" ? "No saved deals yet" : view === "expiring" ? "Nothing is expiring soon" : "No deals in this view";
  const guidance = filtered ? "Clear the current filters to return to your best matches." : view === "saved" ? "Save useful offers and they will stay easy to find here." : "Try another view to browse your active offers.";
  return <div className="flex min-h-52 flex-col items-center justify-center rounded-card border border-dashed border-slate-300 bg-white p-8 text-center"><Tag className="h-8 w-8 text-slate-400" aria-hidden="true"/><h2 className="mt-3 font-semibold text-slate-950">{title}</h2><p className="mt-1 text-sm text-slate-600">{guidance}</p><Button className="mt-4" variant="outline" size="sm" onClick={onClear}>{filtered ? "Clear filters" : "Show best matches"}</Button></div>;
}

function DealsSkeleton() {
  return <div className="grid gap-4 lg:grid-cols-2" aria-label="Loading deals" aria-busy="true">{[0, 1].map((item) => <Card key={item}><CardContent className="space-y-4 p-5"><div className="flex gap-3"><div className="h-11 w-11 animate-pulse rounded-control bg-slate-200"/><div className="flex-1 space-y-2"><div className="h-4 w-28 animate-pulse rounded bg-slate-200"/><div className="h-7 w-40 animate-pulse rounded bg-slate-200"/></div></div><div className="h-6 w-3/4 animate-pulse rounded bg-slate-200"/><div className="h-16 animate-pulse rounded bg-slate-100"/></CardContent></Card>)}</div>;
}

function DealViewButton({ active, onClick, label, count }: { active: boolean; onClick: () => void; label: string; count: number }) {
  return <button type="button" onClick={onClick} aria-current={active ? "page" : undefined} className={`min-h-11 whitespace-nowrap rounded-control px-3 py-2 text-sm font-medium transition focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-indigo-500 ${active ? "bg-indigo-600 text-white" : "text-slate-600 hover:bg-white hover:text-slate-950"}`}>{label}<span className={`ml-2 rounded-full px-1.5 py-0.5 text-xs ${active ? "bg-white/20" : "bg-slate-200/70"}`}>{count}</span></button>;
}

function DealCard({ offer, busy, copied, onCopy, onAction }: { offer: PromotionOffer; busy: boolean; copied: boolean; onCopy: () => void; onAction: (kind: OfferAction) => void }) {
  const value = offerValue(offer);
  const expiry = expiryPresentation(offer.expires_at);
  return <Card className="transition hover:border-indigo-200 hover:shadow-md"><CardContent className="p-5"><div className="flex items-start gap-3"><MerchantAvatar name={offer.merchant} className="h-11 w-11"/><div className="min-w-0 flex-1"><div className="flex flex-wrap items-center gap-2"><p className="truncate text-sm font-semibold text-slate-800">{offer.merchant}</p>{offer.saved ? <Badge variant="default"><Bookmark className="mr-1 h-3 w-3 fill-current" aria-hidden="true"/>Saved</Badge> : null}</div><p className="mt-2 text-3xl font-semibold tracking-tight text-slate-950">{value}</p></div></div><h3 className="mt-4 text-lg font-semibold leading-6 text-slate-950">{offer.headline}</h3>{offer.description ? <p className="mt-2 line-clamp-3 text-sm leading-6 text-slate-600">{offer.description}</p> : null}<div className="mt-3 flex flex-wrap items-center gap-2"><Badge variant={expiry.variant}>{expiry.label}</Badge>{offer.category ? <Badge variant="secondary">{offer.category}</Badge> : null}</div>{offer.promo_code ? <button className="mt-4 inline-flex min-h-11 items-center gap-2 rounded-control bg-slate-100 px-3 py-2 font-mono text-sm text-slate-900 hover:bg-slate-200 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-indigo-500" onClick={onCopy} aria-label={`Copy promo code ${offer.promo_code}`}><span>Code: {offer.promo_code}</span>{copied ? <Check className="h-4 w-4" aria-hidden="true"/> : <Copy className="h-4 w-4" aria-hidden="true"/>}</button> : null}<DealDetails offer={offer} />{offer.why.length ? <div className="mt-4 rounded-control bg-indigo-50 p-3"><p className="flex items-center gap-2 text-sm font-semibold text-indigo-950"><Sparkles className="h-4 w-4" aria-hidden="true"/>Why this matches</p><ul className="mt-1 space-y-1 text-sm leading-5 text-indigo-900">{offer.why.map((reason) => <li key={reason}>• {reason}</li>)}</ul></div> : null}<DealActions offer={offer} busy={busy} onAction={onAction} /></CardContent></Card>;
}

function DealDetails({ offer }: { offer: PromotionOffer }) {
  if (offer.minimum_spend == null && !offer.offer_type && !offer.terms_summary) return null;
  return <details className="mt-4 border-t border-slate-200 pt-3"><summary className="min-h-11 cursor-pointer py-2 text-sm font-medium text-slate-700 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-indigo-500">Terms and conditions</summary><div className="pb-1 text-sm leading-6 text-slate-600">{offer.minimum_spend != null ? <p><span className="font-medium text-slate-800">Minimum spend:</span> ${offer.minimum_spend.toFixed(2)}</p> : null}{offer.offer_type ? <p><span className="font-medium text-slate-800">Offer type:</span> <span className="capitalize">{offer.offer_type.split("_").join(" ")}</span></p> : null}{offer.terms_summary ? <p className="mt-1"><span className="font-medium text-slate-800">Terms:</span> {offer.terms_summary}</p> : null}</div></details>;
}

function DealActions({ offer, busy, onAction }: { offer: PromotionOffer; busy: boolean; onAction: (kind: OfferAction) => void }) {
  return <div className="mt-5 flex flex-wrap items-start gap-2 border-t border-slate-200 pt-4"><DestinationAction offer={offer}/><Button className="min-w-24" variant="outline" onClick={() => onAction("save")} disabled={busy || offer.saved}><Bookmark className="h-4 w-4" aria-hidden="true"/>{offer.saved ? "Saved" : "Save"}</Button><div className="ml-auto"><OverflowMenu label={`More actions for ${offer.merchant}`} items={[{ label: "Dismiss", icon: X, disabled: busy, onSelect: () => onAction("dismiss") }, { label: "Not relevant", disabled: busy, onSelect: () => onAction("not_relevant") }, { label: `Mute ${offer.merchant}`, destructive: true, disabled: busy, onSelect: () => onAction("mute_merchant") }]} /></div></div>;
}

function DestinationAction({ offer, compact = false }: { offer: PromotionOffer; compact?: boolean }) {
  const domain = destinationDomain(offer.destination_url);
  if (!offer.destination_url || !domain) return <span className="text-xs text-slate-500">No destination link</span>;
  if (offer.trust_status === "trusted") return <div className="min-w-0"><Button size={compact ? "sm" : "default"} asChild><a href={offer.destination_url} target="_blank" rel="noopener noreferrer">Open deal<ExternalLink className="h-4 w-4" aria-hidden="true"/></a></Button><p className="mt-1 max-w-40 truncate text-xs text-slate-500" title={domain}>Opens {domain}</p></div>;
  return <Dialog.Root><div className="min-w-0"><Dialog.Trigger asChild><Button size={compact ? "sm" : "default"} variant="outline">Review link<ExternalLink className="h-4 w-4" aria-hidden="true"/></Button></Dialog.Trigger><p className="mt-1 max-w-40 truncate text-xs text-amber-700" title={domain}>Unverified · {domain}</p></div><Dialog.Portal><Dialog.Overlay className="fixed inset-0 z-40 bg-slate-950/45 backdrop-blur-[1px]"/><Dialog.Content className="fixed left-1/2 top-1/2 z-50 w-[calc(100%-2rem)] max-w-md -translate-x-1/2 -translate-y-1/2 rounded-card border border-ui-border bg-white p-6 shadow-primary focus:outline-none"><Dialog.Title className="text-lg font-semibold text-slate-950">Review this destination</Dialog.Title><Dialog.Description className="mt-2 text-sm leading-6 text-slate-600">ExpenseOps could not verify this link against the sender or merchant. Continue only if you recognize the destination.</Dialog.Description><div className="mt-4 rounded-control border border-amber-200 bg-amber-50 p-3"><p className="text-xs font-semibold uppercase tracking-wide text-amber-800">Destination domain</p><p className="mt-1 break-all text-sm font-medium text-amber-950">{domain}</p></div><div className="mt-6 flex flex-col-reverse gap-2 sm:flex-row sm:justify-end"><Dialog.Close asChild><Button variant="outline">Cancel</Button></Dialog.Close><Button asChild><a href={offer.destination_url} target="_blank" rel="noopener noreferrer">Continue to {domain}<ExternalLink className="h-4 w-4" aria-hidden="true"/></a></Button></div><Dialog.Close aria-label="Close destination review" className="absolute right-3 top-3 flex h-11 w-11 items-center justify-center rounded-control text-slate-600 hover:bg-slate-100"><X className="h-5 w-5" aria-hidden="true"/></Dialog.Close></Dialog.Content></Dialog.Portal></Dialog.Root>;
}

function CompactDealRow({ offer, busy, onAction }: { offer: PromotionOffer; busy: boolean; onAction: (kind: OfferAction) => void }) {
  const expiry = expiryPresentation(offer.expires_at);
  return <div className="flex flex-col gap-4 p-4 sm:flex-row sm:items-center sm:justify-between"><div className="flex min-w-0 items-start gap-3"><MerchantAvatar name={offer.merchant}/><div className="min-w-0"><div className="flex flex-wrap items-center gap-2"><p className="text-sm font-semibold text-slate-800">{offer.merchant}</p><Badge variant={expiry.variant}>{expiry.label}</Badge></div><p className="mt-1 text-lg font-semibold text-slate-950">{offerValue(offer)}</p><p className="mt-1 truncate text-sm text-slate-600">{offer.headline}</p></div></div><div className="flex min-w-0 flex-wrap items-start gap-2 sm:shrink-0"><DestinationAction offer={offer} compact/><Button size="sm" variant="outline" onClick={() => onAction("save")} disabled={busy || offer.saved}>{offer.saved ? "Saved" : "Save"}</Button><OverflowMenu label={`More actions for ${offer.merchant}`} items={[{ label: "Dismiss", icon: X, disabled: busy, onSelect: () => onAction("dismiss") }, { label: "Not relevant", disabled: busy, onSelect: () => onAction("not_relevant") }, { label: `Mute ${offer.merchant}`, destructive: true, disabled: busy, onSelect: () => onAction("mute_merchant") }]} /></div></div>;
}

function offerValue(offer: PromotionOffer) {
  if (offer.percent_off != null) return `${offer.percent_off}% off`;
  if (offer.amount_off != null) return `$${offer.amount_off.toFixed(offer.amount_off % 1 ? 2 : 0)} off`;
  if (offer.minimum_spend != null) return `Offer from ${offer.merchant}`;
  return "Featured offer";
}

function destinationDomain(url: string | null) {
  if (!url) return null;
  try {
    return new URL(url).hostname.replace(/^www\./, "");
  } catch {
    return null;
  }
}

function expiryPresentation(value: string | null): { label: string; variant: "secondary" | "warning" | "destructive" } {
  if (!value) return { label: "No expiry provided", variant: "secondary" };
  const expires = new Date(value);
  if (!Number.isFinite(expires.getTime())) return { label: "Expiry unavailable", variant: "secondary" };
  const days = Math.ceil((expires.getTime() - Date.now()) / 86_400_000);
  const date = expires.toLocaleDateString(undefined, { month: "short", day: "numeric", year: expires.getFullYear() !== new Date().getFullYear() ? "numeric" : undefined });
  if (days < 0) return { label: `Expired ${date}`, variant: "destructive" };
  if (days === 0) return { label: "Expires today", variant: "destructive" };
  if (days === 1) return { label: "Expires tomorrow", variant: "destructive" };
  if (days <= 7) return { label: `Expires in ${days} days`, variant: "warning" };
  return { label: `Expires ${date}`, variant: "secondary" };
}
