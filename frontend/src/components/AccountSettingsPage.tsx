import { useEffect, useRef, useState, type ReactNode } from "react";
import { Brain, Building2, CheckCircle2, ChevronRight, Circle, Copy, ExternalLink, Link2, MessageCircle, Pencil, Plus, Settings2, Shield, SlidersHorizontal, Unplug, UserMinus, UserRound, Users, UsersRound, WalletCards } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { PageHeader } from "@/components/ui/page-header";
import { ApiError, api, apiErrorMessage } from "@/lib/api";
import { onboardingChecklist } from "@/onboardingLogic";

type AccountContext = {
  user: { id: number; email: string; display_name: string; avatar_url?: string | null };
  workspace: { id: number; name: string; workspace_type: string };
};
type Workspace = { id: number; name: string; role: string; current: boolean };
type Member = { user_id: number; email: string; display_name: string; role: string };
type Integrations = {
  gmail: { connected: boolean; identity?: string | null };
  plaid: { connected: boolean; institutions: { id: number; name: string; owner_user_id?: number | null; owner_name?: string | null; ownership_verified: boolean; is_mine: boolean }[] };
  telegram: { connected: boolean; telegram_user_id?: string | null; chat_id?: string | null };
  splitwise: { connected: boolean; available: boolean; identity?: string | null; email?: string | null; verified?: boolean };
  google_maps: { connected: boolean; managed_by: string };
  openai: { connected: boolean; managed_by: string };
};
type OnboardingStatus = { complete: boolean };
type SettingsSection = "account" | "workspace" | "personal" | "workspace-connections" | "expense" | "splitwise" | "learning" | "privacy";

const settingsSections: Array<{ value: SettingsSection; label: string; description: string; icon: typeof Settings2 }> = [
  { value: "account", label: "Account", description: "Identity and setup", icon: UserRound },
  { value: "workspace", label: "Workspace & members", description: "Households and access", icon: UsersRound },
  { value: "personal", label: "Personal connections", description: "Your notification channels", icon: MessageCircle },
  { value: "workspace-connections", label: "Workspace connections", description: "Shared data providers", icon: Building2 },
  { value: "expense", label: "Expense preferences", description: "Review safeguards", icon: SlidersHorizontal },
  { value: "splitwise", label: "Splitwise groups", description: "Groups and participants", icon: Users },
  { value: "learning", label: "Learned behavior", description: "Correctable memory", icon: Brain },
  { value: "privacy", label: "Privacy & account", description: "Data and access actions", icon: Shield },
];

export function AccountSettingsPage({ context, splitwiseTools, learnedBehaviorTools }: { context: AccountContext; splitwiseTools?: ReactNode; learnedBehaviorTools?: ReactNode }) {
  const [workspaces, setWorkspaces] = useState<Workspace[]>([]);
  const [integrations, setIntegrations] = useState<Integrations | null>(null);
  const [workspaceName, setWorkspaceName] = useState("");
  const [renameValue, setRenameValue] = useState(context.workspace.name);
  const [members, setMembers] = useState<Member[]>([]);
  const [inviteEmail, setInviteEmail] = useState("");
  const [inviteLink, setInviteLink] = useState("");
  const [telegramSetup, setTelegramSetup] = useState<{ command: string; connect_url: string | null } | null>(null);
  const [message, setMessage] = useState("");
  const [integrationError, setIntegrationError] = useState("");
  const [onboarding, setOnboarding] = useState<OnboardingStatus | null>(null);
  const [section, setSection] = useState<SettingsSection>("account");
  const [busyAction, setBusyAction] = useState<string | null>(null);
  const pendingActions = useRef(new Set<string>());

  async function performAction<T>(key: string, work: () => Promise<T>, success?: string) {
    if (pendingActions.current.has(key)) return undefined;
    pendingActions.current.add(key);
    setBusyAction(key);
    setMessage("");
    setIntegrationError("");
    try {
      const result = await work();
      if (success) setMessage(success);
      return result;
    } catch (error) {
      const supportId = error instanceof ApiError ? ` Support ID: ${error.correlationId}` : "";
      setIntegrationError(`${apiErrorMessage(error, "This change could not be completed.")}${supportId}`);
      return undefined;
    } finally {
      pendingActions.current.delete(key);
      setBusyAction((current) => current === key ? null : current);
    }
  }

  async function load() {
    const [workspaceValues, integrationValues, memberValues, onboardingValue] = await Promise.all([
      api<Workspace[]>("/api/workspaces"),
      api<Integrations>("/api/integrations"),
      api<Member[]>(`/api/workspaces/${context.workspace.id}/members`),
      api<OnboardingStatus>("/api/integrations/onboarding"),
    ]);
    setWorkspaces(workspaceValues);
    setIntegrations(integrationValues);
    setMembers(memberValues);
    setOnboarding(onboardingValue);
  }

  useEffect(() => {
    void load().catch((error) => {
      const supportId = error instanceof ApiError ? ` Support ID: ${error.correlationId}` : "";
      setIntegrationError(`${apiErrorMessage(error, "Settings could not be loaded.")}${supportId}`);
    });
  }, []);

  useEffect(() => {
    if (!telegramSetup) return;
    const interval = window.setInterval(() => {
      void api<Integrations>("/api/integrations")
        .then((value) => {
          setIntegrations(value);
          if (value.telegram.connected) {
            setTelegramSetup(null);
            setMessage("Telegram connected successfully.");
          }
        })
        .catch(() => undefined);
    }, 3000);
    return () => window.clearInterval(interval);
  }, [telegramSetup]);

  async function createWorkspace() {
    await performAction("create-workspace", async () => {
      await api("/api/workspaces", { method: "POST", body: JSON.stringify({ name: workspaceName }) });
      setWorkspaceName("");
      await load();
    }, "Workspace created.");
  }

  async function switchWorkspace(id: number) {
    await performAction(`switch-workspace-${id}`, async () => {
      await api(`/api/workspaces/${id}/switch`, { method: "POST" });
      window.location.reload();
    });
  }

  async function renameWorkspace() {
    await performAction("rename-workspace", async () => {
      await api(`/api/workspaces/${context.workspace.id}`, {
        method: "PATCH",
        body: JSON.stringify({ name: renameValue }),
      });
      await load();
    }, "Workspace renamed.");
  }

  async function leaveWorkspace() {
    if (!window.confirm(`Leave ${context.workspace.name}?`)) return;
    await performAction("leave-workspace", async () => {
      await api(`/api/workspaces/${context.workspace.id}/membership`, { method: "DELETE" });
      window.location.reload();
    });
  }

  async function invite() {
    await performAction("invite-member", async () => {
      const value = await api<{ invite_token: string }>(
        `/api/workspaces/${context.workspace.id}/invitations`,
        { method: "POST", body: JSON.stringify({ email: inviteEmail, role: "member" }) },
      );
      setInviteLink(`${window.location.origin}/?invite=${encodeURIComponent(value.invite_token)}`);
    }, "Invitation link created. Share it privately with the invited person.");
  }

  async function removeMember(member: Member) {
    if (!window.confirm(`Remove ${member.display_name} from ${context.workspace.name}? They will immediately lose access to this workspace.`)) return;
    await performAction(`remove-member-${member.user_id}`, async () => {
      await api(`/api/workspaces/${context.workspace.id}/members/${member.user_id}`, { method: "DELETE" });
      await load();
    }, `${member.display_name} was removed from the workspace.`);
  }

  async function transferOwnership(member: Member) {
    if (!window.confirm(`Transfer ownership of ${context.workspace.name} to ${member.display_name}? You will become a member and only the new owner can reverse this change.`)) return;
    await performAction(`transfer-owner-${member.user_id}`, async () => {
      await api(`/api/workspaces/${context.workspace.id}/members/${member.user_id}/transfer-ownership`, { method: "POST" });
      await load();
    }, `Ownership was transferred to ${member.display_name}.`);
  }

  async function connectGmail() {
    await performAction("connect-gmail", async () => {
      const value = await api<{ authorization_url: string }>("/api/integrations/gmail/connect", {
        method: "POST",
      });
      window.location.assign(value.authorization_url);
    });
  }

  async function connectTelegram() {
    await performAction("connect-telegram", async () => {
      const value = await api<{ command: string; connect_url: string | null }>("/api/integrations/telegram/link-code", {
        method: "POST",
      });
      setTelegramSetup(value);
    });
  }

  async function connectSplitwise() {
    await performAction("connect-splitwise", async () => {
      const value = await api<{ authorize_url: string }>("/splitwise/oauth/authorize");
      window.location.assign(value.authorize_url);
    });
  }

  async function disconnect(path: string) {
    await performAction(`disconnect-${path}`, async () => {
      await api(path, { method: "DELETE" });
      await load();
    }, "Integration disconnected. Your existing history was retained.");
  }

  async function claimBank(id: number, name: string) {
    if (!window.confirm(`Confirm that the ${name} connection belongs to you? Its transactions will use your personal Splitwise payer identity.`)) return;
    await performAction(`claim-plaid-${id}`, async () => {
      await api(`/api/integrations/plaid/${id}/claim`, { method: "POST" });
      await load();
    }, `${name} is now attributed to your ExpenseOps identity.`);
  }

  const checklist = integrations ? onboardingChecklist(integrations) : [];
  const currentMembership = workspaces.find((workspace) => workspace.current);
  const canManageWorkspace = currentMembership?.role === "owner";
  const completedSetupSteps = 2 + checklist.filter((item) => item.done).length;

  return (
    <div className="space-y-5">
      <PageHeader
        eyebrow={<span className="inline-flex items-center gap-2"><Settings2 className="h-4 w-4" aria-hidden="true" />Settings</span>}
        title="Settings"
        description="Manage identity, access, connections, expense tools, and data controls."
      />
      {integrationError ? <p role="alert" className="rounded-xl border border-rose-200 bg-rose-50 p-3 text-sm text-rose-800">{integrationError}</p> : null}
      {message ? <p role="status" className="rounded-xl border border-emerald-200 bg-emerald-50 p-3 text-sm text-emerald-800">{message}</p> : null}

      <MobileSettingsNavigation selected={section} onChange={setSection} />
      <div className="grid items-start gap-5 lg:grid-cols-[240px_minmax(0,1fr)]">
        <SettingsNavigation selected={section} onChange={setSection} />
        <main id="settings-panel" className="min-w-0" aria-live="polite">
          {section === "account" ? <SettingsPanel title="Account" description="Your signed-in identity and setup progress.">
            <Card>
              <CardContent className="grid gap-5 p-5 sm:grid-cols-2 sm:divide-x sm:divide-slate-200">
                <div className="flex flex-col gap-4 sm:flex-row sm:items-center"><span className="flex h-14 w-14 shrink-0 items-center justify-center rounded-2xl bg-indigo-50 text-lg font-semibold text-indigo-700 ring-1 ring-indigo-100">{initials(context.user.display_name)}</span><div className="min-w-0"><p className="text-xs font-semibold uppercase tracking-wide text-slate-500">Signed in as</p><p className="mt-1 font-semibold text-slate-950">{context.user.display_name}</p><p className="mt-1 truncate text-sm text-slate-600">{context.user.email}</p><p className="mt-2 text-xs text-slate-500">ExpenseOps account ID {context.user.id}</p></div></div>
                <div className="flex items-center gap-4 sm:pl-5"><span className="flex h-11 w-11 shrink-0 items-center justify-center rounded-xl bg-slate-100 text-slate-600"><Building2 className="h-5 w-5" /></span><div className="min-w-0"><p className="text-xs font-semibold uppercase tracking-wide text-slate-500">Current workspace</p><p className="mt-1 truncate font-semibold text-slate-950">{context.workspace.name}</p><p className="mt-1 text-sm capitalize text-slate-600">{currentMembership?.role || "Loading role…"} · {context.workspace.workspace_type}</p></div></div>
              </CardContent>
            </Card>
            {onboarding && !onboarding.complete ? <Card>
              <CardHeader><div className="flex items-start justify-between gap-4"><div><CardTitle>Finish your setup</CardTitle><CardDescription>Connect only the services you want. You can return here at any time.</CardDescription></div><span className="shrink-0 text-sm font-semibold text-indigo-700">{completedSetupSteps}/6</span></div><div className="h-2 overflow-hidden rounded-full bg-slate-100"><div className="h-full rounded-full bg-indigo-600" style={{ width: `${completedSetupSteps / 6 * 100}%` }} /></div></CardHeader>
              <CardContent className="grid gap-2 sm:grid-cols-2"><ChecklistRow done label="Account created" detail={context.user.email} /><ChecklistRow done label="Workspace created" detail={context.workspace.name} />{checklist.map(({ label, done, detail }) => <ChecklistRow key={label} done={done} label={label} detail={detail} />)}</CardContent>
            </Card> : null}
          </SettingsPanel> : null}

          {section === "workspace" ? <SettingsPanel title="Workspace and members" description="Switch workspaces, manage the current household, and invite people.">
            <div className="grid gap-5 xl:grid-cols-2">
              <Card><CardHeader><CardTitle>Workspaces</CardTitle><CardDescription>Switch among workspaces where you are a member.</CardDescription></CardHeader><CardContent className="grid gap-3">
                {workspaces.map((workspace) => <button key={workspace.id} type="button" disabled={Boolean(busyAction)} onClick={() => !workspace.current && switchWorkspace(workspace.id)} className="flex min-h-11 items-center justify-between rounded-lg border border-slate-200 px-3 py-2 text-left hover:border-indigo-300 disabled:cursor-not-allowed disabled:opacity-60 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-indigo-500"><span><span className="font-medium text-slate-950">{workspace.name}</span><span className="ml-2 text-xs capitalize text-slate-600">{workspace.role}</span></span>{workspace.current ? <CheckCircle2 className="h-4 w-4 text-emerald-600" /> : <ChevronRight className="h-4 w-4 text-slate-400" />}</button>)}
                <div className="flex flex-col gap-2 sm:flex-row"><Input value={workspaceName} onChange={(event) => setWorkspaceName(event.target.value)} placeholder="New workspace name" /><Button disabled={!workspaceName.trim() || busyAction === "create-workspace"} onClick={createWorkspace}><Plus className="h-4 w-4" />{busyAction === "create-workspace" ? "Creating…" : "Create"}</Button></div>
                {canManageWorkspace ? <div className="flex flex-col gap-2 border-t border-slate-200 pt-3 sm:flex-row"><Input aria-label="Current workspace name" value={renameValue} onChange={(event) => setRenameValue(event.target.value)} /><Button variant="outline" disabled={!renameValue.trim() || renameValue.trim() === context.workspace.name || busyAction === "rename-workspace"} onClick={renameWorkspace}><Pencil className="h-4 w-4" />{busyAction === "rename-workspace" ? "Renaming…" : "Rename"}</Button></div> : null}
              </CardContent></Card>
              <Card><CardHeader><CardTitle>Members</CardTitle><CardDescription>{canManageWorkspace ? "Invite someone using a single-use, seven-day link." : "Member access is managed by a workspace owner."}</CardDescription></CardHeader><CardContent className="grid gap-3">
                <div className="grid gap-2">{members.map((member) => <div key={member.user_id} className="flex min-h-11 flex-col justify-between gap-3 rounded-lg border border-slate-200 px-3 py-2 sm:flex-row sm:items-center"><span className="min-w-0"><span className="block truncate font-medium text-slate-950">{member.display_name}{member.user_id === context.user.id ? " (you)" : ""}</span><span className="block truncate text-sm text-slate-600">{member.email}</span></span><div className="flex flex-wrap items-center gap-2"><span className="text-xs font-semibold uppercase tracking-wide text-slate-600">{member.role}</span>{canManageWorkspace && member.user_id !== context.user.id ? <><Button size="sm" variant="outline" disabled={Boolean(busyAction)} onClick={() => transferOwnership(member)}>{busyAction === `transfer-owner-${member.user_id}` ? "Transferring…" : "Make owner"}</Button><Button size="sm" variant="outline" disabled={Boolean(busyAction)} className="border-rose-300 text-rose-700 hover:bg-rose-50 hover:text-rose-800" onClick={() => removeMember(member)}><UserMinus className="h-4 w-4" />{busyAction === `remove-member-${member.user_id}` ? "Removing…" : "Remove"}</Button></> : null}</div></div>)}</div>
                {canManageWorkspace ? <><div className="flex flex-col gap-2 sm:flex-row"><Input type="email" value={inviteEmail} onChange={(event) => setInviteEmail(event.target.value)} placeholder="friend@example.com" /><Button disabled={!inviteEmail.includes("@") || busyAction === "invite-member"} onClick={invite}><Users className="h-4 w-4" />{busyAction === "invite-member" ? "Creating link…" : "Invite"}</Button></div>{inviteLink ? <CopyValue label="Share this invitation privately" value={inviteLink} /> : null}</> : null}
              </CardContent></Card>
            </div>
          </SettingsPanel> : null}

          {section === "personal" ? <SettingsPanel title="Personal connections" description="Services tied to you as an individual, not every workspace member.">
            <Card><CardHeader><CardTitle>Your accounts</CardTitle><CardDescription>Each member connects their own bank, Splitwise payer, and Telegram delivery identity.</CardDescription></CardHeader><CardContent className="space-y-3">
              <IntegrationRow name="Telegram" mark="TG" scope="Personal" connected={integrations?.telegram.connected} connectedDetail={integrations?.telegram.telegram_user_id ? `Telegram user ${integrations.telegram.telegram_user_id} · chat ${integrations.telegram.chat_id}` : "Connected to your ExpenseOps identity."} busy={busyAction === "connect-telegram" || busyAction === "disconnect-/api/integrations/telegram"} onConnect={connectTelegram} onDisconnect={() => disconnect("/api/integrations/telegram")} />
              {telegramSetup ? <TelegramSetup value={telegramSetup} /> : null}
              <IntegrationRow name="Plaid" mark="P" scope="Personal" connected={integrations?.plaid.institutions.some((value) => value.is_mine)} connectedDetail={integrations?.plaid.institutions.filter((value) => value.is_mine).map((value) => value.name).join(", ") || "Connect your bank so ExpenseOps can attribute its transactions to you."} onConnect={() => window.location.assign("/?workspace=expenses&connect=plaid")} />
              {integrations?.plaid.institutions.map((institution) => <div key={institution.id} className="flex min-h-11 flex-col justify-between gap-2 rounded-lg bg-slate-50 px-3 py-2 text-sm sm:flex-row sm:items-center"><span className="min-w-0"><span className="block truncate font-medium text-slate-950">{institution.name}</span><span className="block text-xs text-slate-600">{institution.owner_name ? `${institution.owner_name}${institution.ownership_verified ? " · verified owner" : " · verification needed"}` : "Owner not confirmed"}</span></span><div className="flex gap-2">{!institution.owner_user_id ? <Button size="sm" variant="outline" disabled={Boolean(busyAction)} onClick={() => claimBank(institution.id, institution.name)}>{busyAction === `claim-plaid-${institution.id}` ? "Confirming…" : "Confirm mine"}</Button> : null}{institution.is_mine || canManageWorkspace ? <Button size="sm" variant="outline" disabled={busyAction === `disconnect-/api/integrations/plaid/${institution.id}`} onClick={() => disconnect(`/api/integrations/plaid/${institution.id}`)}><Unplug className="h-4 w-4" />{busyAction === `disconnect-/api/integrations/plaid/${institution.id}` ? "Disconnecting…" : "Disconnect"}</Button> : null}</div></div>)}
              <IntegrationRow name="Splitwise" mark="S" scope="Personal" connected={integrations?.splitwise.connected} available={integrations?.splitwise.available} unavailableDetail="Splitwise sign-in is not configured by the application administrator." connectedDetail={integrations?.splitwise.identity ? `${integrations.splitwise.identity}${integrations.splitwise.email ? ` · ${integrations.splitwise.email}` : ""}${integrations.splitwise.verified ? " · verified payer" : " · reconnect to verify payer"}` : "Connect your personal Splitwise account."} busy={busyAction === "connect-splitwise" || busyAction === "disconnect-/api/integrations/splitwise"} onConnect={connectSplitwise} onDisconnect={() => disconnect("/api/integrations/splitwise")} />
            </CardContent></Card>
          </SettingsPanel> : null}

          {section === "workspace-connections" ? <SettingsPanel title="Workspace connections" description="Shared data sources and application-managed providers for this workspace.">
            <Card><CardContent className="grid gap-3 p-4 sm:p-5">
              <IntegrationRow name="Gmail" mark="G" scope="Workspace" connected={integrations?.gmail.connected} connectedDetail={integrations?.gmail.identity ? `Connected as ${integrations.gmail.identity}` : "Connected workspace receipt inbox."} ownerManaged={!canManageWorkspace} busy={busyAction === "connect-gmail" || busyAction === "disconnect-/api/integrations/gmail"} onConnect={canManageWorkspace ? connectGmail : undefined} onDisconnect={canManageWorkspace ? () => disconnect("/api/integrations/gmail") : undefined} />
              <IntegrationRow name="Google Maps" mark="M" scope="Application" connected connectedDetail="Application-managed routing and place search." />
              <IntegrationRow name="OpenAI" mark="AI" scope="Application" connected connectedDetail="Application-managed language and receipt processing." />
            </CardContent></Card>
          </SettingsPanel> : null}

          {section === "expense" ? <SettingsPanel title="Expense preferences" description="Understand the safeguards applied to daily expense review.">
            <Card><CardContent className="divide-y divide-slate-200 p-0"><PreferenceFact icon={WalletCards} title="Review before posting" detail="Shared expenses require a valid participant and allocation before they can be posted." /><PreferenceFact icon={Shield} title="Pending transactions" detail="Bank-pending transactions are not sent to Splitwise." /><PreferenceFact icon={SlidersHorizontal} title="Decision controls" detail="Personal and Split remain the primary review actions; drafts stay available under More actions." /></CardContent></Card>
          </SettingsPanel> : null}

          {section === "splitwise" ? <SettingsPanel title="Splitwise groups" description="Create groups, find friends, invite participants, and manage settled members.">
            {splitwiseTools || <UnavailableSettings title="Splitwise tools are unavailable" detail="Connect Splitwise in Workspace connections first." />}
          </SettingsPanel> : null}

          {section === "learning" ? <SettingsPanel title="Learned behavior" description="Review saved people, groups, and fallback memories used to assist future decisions.">
            {learnedBehaviorTools || <UnavailableSettings title="No learned behavior yet" detail="ExpenseOps will surface correctable memories after you review transactions." />}
          </SettingsPanel> : null}

          {section === "privacy" ? <SettingsPanel title="Privacy and account actions" description="Understand where data is used and access irreversible workspace actions.">
            <Card><CardHeader><CardTitle>Data boundaries</CardTitle><CardDescription>Connections are used only for the current ExpenseOps workspace unless labeled Personal or Application above.</CardDescription></CardHeader><CardContent className="space-y-2 text-sm leading-6 text-slate-700"><p>Disconnect a provider from its connection section to stop future imports. Existing transaction and learning history is retained unless a deletion workflow explicitly says otherwise.</p><p>Self-service account deletion and retention controls are not yet available in this build; they remain a launch blocker tracked in the readiness plan.</p></CardContent></Card>
            <Card className="border-rose-200"><CardHeader><div className="inline-flex w-fit items-center gap-2 rounded-full bg-rose-50 px-2.5 py-1 text-xs font-semibold uppercase tracking-wide text-rose-700"><Shield className="h-3.5 w-3.5" />Danger zone</div><CardTitle>Leave this workspace</CardTitle><CardDescription>Your access will be removed. Shared workspace data is not deleted.</CardDescription></CardHeader><CardContent><Button variant="outline" disabled={busyAction === "leave-workspace"} className="border-rose-300 text-rose-700 hover:bg-rose-50 hover:text-rose-800" onClick={leaveWorkspace}><UserMinus className="h-4 w-4" />{busyAction === "leave-workspace" ? "Leaving…" : `Leave ${context.workspace.name}`}</Button></CardContent></Card>
          </SettingsPanel> : null}
        </main>
      </div>
    </div>
  );
}

function SettingsNavigation({ selected, onChange }: { selected: SettingsSection; onChange: (value: SettingsSection) => void }) {
  return <aside className="sticky top-2 hidden rounded-xl border border-slate-200 bg-white p-2 shadow-sm lg:block" aria-label="Settings categories"><nav className="space-y-1">{settingsSections.map(({ value, label, description, icon: Icon }) => <button key={value} type="button" aria-current={selected === value ? "page" : undefined} onClick={() => onChange(value)} className={`flex min-h-14 w-full items-center gap-3 rounded-lg px-3 py-2 text-left transition focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-indigo-500 ${selected === value ? "bg-indigo-50 text-indigo-900" : "text-slate-700 hover:bg-slate-50 hover:text-slate-950"}`}><Icon className={`h-4 w-4 shrink-0 ${selected === value ? "text-indigo-600" : "text-slate-500"}`} /><span className="min-w-0 flex-1"><span className="block truncate text-sm font-semibold">{label}</span><span className={`block truncate text-xs ${selected === value ? "text-indigo-700" : "text-slate-600"}`}>{description}</span></span>{selected === value ? <ChevronRight className="h-4 w-4 shrink-0 text-indigo-500" /> : null}</button>)}</nav></aside>;
}

function MobileSettingsNavigation({ selected, onChange }: { selected: SettingsSection; onChange: (value: SettingsSection) => void }) {
  return <label className="grid gap-1.5 rounded-xl border border-slate-200 bg-white p-3 text-xs font-semibold text-slate-700 shadow-sm lg:hidden">Settings section<select className="h-11 rounded-lg border border-slate-300 bg-white px-3 text-sm font-medium text-slate-900 focus:border-indigo-500 focus:outline-none focus:ring-2 focus:ring-indigo-500/20" value={selected} onChange={(event) => onChange(event.target.value as SettingsSection)}>{settingsSections.map((item) => <option key={item.value} value={item.value}>{item.label}</option>)}</select></label>;
}

function SettingsPanel({ title, description, children }: { title: string; description: string; children: ReactNode }) {
  return <section className="space-y-4" aria-labelledby={`settings-${title.toLowerCase().replace(/[^a-z0-9]+/g, "-")}`}><div><h2 id={`settings-${title.toLowerCase().replace(/[^a-z0-9]+/g, "-")}`} className="text-xl font-semibold text-slate-950">{title}</h2><p className="mt-1 text-sm text-slate-600">{description}</p></div>{children}</section>;
}

function PreferenceFact({ icon: Icon, title, detail }: { icon: typeof Settings2; title: string; detail: string }) {
  return <div className="flex items-start gap-3 p-4 sm:p-5"><span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-slate-100 text-slate-600"><Icon className="h-4 w-4" /></span><div><h3 className="text-sm font-semibold text-slate-950">{title}</h3><p className="mt-1 text-sm leading-6 text-slate-600">{detail}</p></div></div>;
}

function UnavailableSettings({ title, detail }: { title: string; detail: string }) {
  return <div className="rounded-xl border border-dashed border-slate-300 bg-white px-5 py-8 text-center"><Settings2 className="mx-auto h-5 w-5 text-slate-500" /><h3 className="mt-2 text-sm font-semibold text-slate-950">{title}</h3><p className="mt-1 text-sm text-slate-600">{detail}</p></div>;
}

function ChecklistRow({ done, label, detail }: { done: boolean; label: string; detail: string }) {
  const Icon = done ? CheckCircle2 : Circle;
  return <div className="flex items-start gap-3 rounded-lg border border-slate-200 p-3"><Icon className={`mt-0.5 h-5 w-5 ${done ? "text-emerald-600" : "text-slate-400"}`} /><div><p className="font-medium text-slate-950">{label}</p><p className="text-sm text-slate-600">{detail}</p></div></div>;
}

function IntegrationRow({ name, mark, scope, connected, available = true, connectedDetail, unavailableDetail, ownerManaged = false, busy = false, onConnect, onDisconnect }: { name: string; mark: string; scope: "Personal" | "Workspace" | "Application"; connected?: boolean; available?: boolean; connectedDetail?: string; unavailableDetail?: string; ownerManaged?: boolean; busy?: boolean; onConnect?: () => void; onDisconnect?: () => void }) {
  const status = connected ? connectedDetail || "Connected" : available ? "Not connected" : unavailableDetail || "Connection is not available yet.";
  return <div className="flex flex-col gap-3 rounded-xl border border-slate-200 p-3 sm:flex-row sm:items-center sm:justify-between"><div className="flex min-w-0 items-start gap-3"><span className="flex h-10 w-10 shrink-0 items-center justify-center rounded-xl bg-slate-950 text-xs font-bold text-white">{mark}</span><div className="min-w-0"><div className="flex flex-wrap items-center gap-2"><p className="font-medium text-slate-950">{name}</p><span className="rounded-full bg-slate-100 px-2 py-0.5 text-[11px] font-semibold text-slate-600">{scope}</span>{connected ? <span className="inline-flex items-center gap-1 text-xs font-medium text-emerald-700"><CheckCircle2 className="h-3.5 w-3.5" />Connected</span> : null}</div><p className="mt-1 text-sm leading-5 text-slate-600">{status}</p></div></div><div className="shrink-0">{onConnect && !connected && available ? <Button size="sm" disabled={busy} onClick={onConnect}><Link2 className="h-4 w-4" />{busy ? "Connecting…" : "Connect"}</Button> : onConnect && !connected ? <span className="rounded-full bg-slate-100 px-2.5 py-1 text-xs font-medium text-slate-600">Setup needed</span> : onDisconnect && connected ? <Button size="sm" variant="outline" disabled={busy} onClick={onDisconnect}><Unplug className="h-4 w-4" />{busy ? "Disconnecting…" : "Disconnect"}</Button> : ownerManaged ? <span className="rounded-full bg-slate-100 px-2.5 py-1 text-xs font-medium text-slate-600">Owner managed</span> : null}</div></div>;
}

function initials(value: string) {
  return value.split(/\s+/).filter(Boolean).slice(0, 2).map((part) => part[0]?.toUpperCase()).join("") || "U";
}

function TelegramSetup({ value }: { value: { command: string; connect_url: string | null } }) {
  return (
    <div className="grid gap-3 rounded-lg border border-indigo-200 bg-indigo-50/60 p-4">
      <div className="flex items-start gap-3">
        <span className="rounded-full bg-indigo-100 p-2 text-indigo-700"><MessageCircle className="h-5 w-5" /></span>
        <div>
          <p className="font-semibold text-slate-950">Finish connecting in Telegram</p>
          <p className="text-sm text-slate-700">Open the bot, tap <strong>Start</strong>, and wait for the connected confirmation. This page checks automatically. The link expires in 10 minutes.</p>
        </div>
      </div>
      {value.connect_url ? (
        <Button asChild className="w-full sm:w-fit">
          <a href={value.connect_url} target="_blank" rel="noreferrer">
            Open Telegram and connect <ExternalLink className="h-4 w-4" />
          </a>
        </Button>
      ) : (
        <p className="text-sm text-amber-800">The direct bot link is not configured. Use the fallback command below.</p>
      )}
      <details className="text-sm text-slate-700">
        <summary className="cursor-pointer rounded focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-indigo-500">Having trouble? Use the command instead</summary>
        <div className="mt-2"><CopyValue label="Send this command to the ExpenseOps bot" value={value.command} /></div>
      </details>
    </div>
  );
}

function CopyValue({ label, value }: { label: string; value: string }) {
  const [copyStatus, setCopyStatus] = useState<"idle" | "copied" | "failed">("idle");
  async function copy() {
    try {
      await navigator.clipboard.writeText(value);
      setCopyStatus("copied");
    } catch {
      setCopyStatus("failed");
    }
  }
  return <div className="rounded-lg bg-slate-100 p-3"><p className="mb-1 text-xs font-semibold uppercase tracking-wide text-slate-600">{label}</p><div className="flex items-center gap-2"><code className="min-w-0 flex-1 truncate text-sm">{value}</code><Button size="sm" variant="outline" aria-label={`Copy ${label}`} onClick={copy}><Copy className="h-4 w-4" />{copyStatus === "copied" ? "Copied" : "Copy"}</Button></div>{copyStatus === "failed" ? <p role="alert" className="mt-2 text-xs font-medium text-rose-700">Copy was blocked. Select the value and copy it manually.</p> : copyStatus === "copied" ? <p role="status" className="sr-only">Copied to clipboard.</p> : null}</div>;
}
