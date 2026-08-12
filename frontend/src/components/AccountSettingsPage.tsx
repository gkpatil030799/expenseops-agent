import { useEffect, useState } from "react";
import { CheckCircle2, Circle, Copy, Link2, LogOut, Pencil, Plus, Unplug, UserMinus, Users } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { api } from "@/lib/api";
import { onboardingChecklist } from "@/onboardingLogic";

type AccountContext = {
  user: { id: number; email: string; display_name: string; avatar_url?: string | null };
  workspace: { id: number; name: string; workspace_type: string };
};
type Workspace = { id: number; name: string; role: string; current: boolean };
type Member = { user_id: number; email: string; display_name: string; role: string };
type Integrations = {
  gmail: { connected: boolean };
  plaid: { connected: boolean; institutions: { id: number; name: string }[] };
  telegram: { connected: boolean };
  splitwise: { connected: boolean };
  google_maps: { connected: boolean; managed_by: string };
  openai: { connected: boolean; managed_by: string };
};

export function AccountSettingsPage({ context }: { context: AccountContext }) {
  const [workspaces, setWorkspaces] = useState<Workspace[]>([]);
  const [integrations, setIntegrations] = useState<Integrations | null>(null);
  const [workspaceName, setWorkspaceName] = useState("");
  const [renameValue, setRenameValue] = useState(context.workspace.name);
  const [members, setMembers] = useState<Member[]>([]);
  const [inviteEmail, setInviteEmail] = useState("");
  const [inviteLink, setInviteLink] = useState("");
  const [telegramCommand, setTelegramCommand] = useState("");
  const [message, setMessage] = useState("");

  async function load() {
    const [workspaceValues, integrationValues, memberValues] = await Promise.all([
      api<Workspace[]>("/api/workspaces"),
      api<Integrations>("/api/integrations"),
      api<Member[]>(`/api/workspaces/${context.workspace.id}/members`),
    ]);
    setWorkspaces(workspaceValues);
    setIntegrations(integrationValues);
    setMembers(memberValues);
  }

  useEffect(() => void load(), []);

  async function createWorkspace() {
    await api("/api/workspaces", { method: "POST", body: JSON.stringify({ name: workspaceName }) });
    setWorkspaceName("");
    await load();
  }

  async function switchWorkspace(id: number) {
    await api(`/api/workspaces/${id}/switch`, { method: "POST" });
    window.location.reload();
  }

  async function renameWorkspace() {
    await api(`/api/workspaces/${context.workspace.id}`, {
      method: "PATCH",
      body: JSON.stringify({ name: renameValue }),
    });
    setMessage("Workspace renamed.");
    await load();
  }

  async function leaveWorkspace() {
    if (!window.confirm(`Leave ${context.workspace.name}?`)) return;
    await api(`/api/workspaces/${context.workspace.id}/membership`, { method: "DELETE" });
    window.location.reload();
  }

  async function invite() {
    const value = await api<{ invite_token: string }>(
      `/api/workspaces/${context.workspace.id}/invitations`,
      { method: "POST", body: JSON.stringify({ email: inviteEmail, role: "member" }) },
    );
    setInviteLink(`${window.location.origin}/?invite=${encodeURIComponent(value.invite_token)}`);
  }

  async function connectGmail() {
    const value = await api<{ authorization_url: string }>("/api/integrations/gmail/connect", {
      method: "POST",
    });
    window.location.assign(value.authorization_url);
  }

  async function connectTelegram() {
    const value = await api<{ command: string }>("/api/integrations/telegram/link-code", {
      method: "POST",
    });
    setTelegramCommand(value.command);
  }

  async function connectSplitwise() {
    const value = await api<{ authorize_url: string }>("/splitwise/oauth/authorize");
    window.location.assign(value.authorize_url);
  }

  async function disconnect(path: string) {
    await api(path, { method: "DELETE" });
    setMessage("Integration disconnected. Your existing history was retained.");
    await load();
  }

  async function logout() {
    await fetch("/auth/logout", { method: "POST", credentials: "same-origin" });
    window.location.reload();
  }

  const checklist = integrations ? onboardingChecklist(integrations) : [];

  return (
    <div className="grid gap-5 xl:grid-cols-2">
      <Card>
        <CardHeader>
          <CardTitle>Welcome to ExpenseOps</CardTitle>
          <CardDescription>Your account and personal workspace are ready. Connect only what you want.</CardDescription>
        </CardHeader>
        <CardContent className="grid gap-3">
          <ChecklistRow done label="Account created" detail={context.user.email} />
          <ChecklistRow done label="Personal workspace created" detail={context.workspace.name} />
          {checklist.map(({ label, done, detail }) => (
            <ChecklistRow key={label} done={done} label={label} detail={detail} />
          ))}
        </CardContent>
      </Card>

      <Card>
        <CardHeader><CardTitle>Integrations</CardTitle><CardDescription>Connections apply only to this workspace.</CardDescription></CardHeader>
        <CardContent className="grid gap-3">
          <IntegrationRow name="Gmail" connected={integrations?.gmail.connected} onConnect={connectGmail} onDisconnect={() => disconnect("/api/integrations/gmail")} />
          <IntegrationRow name="Plaid" connected={integrations?.plaid.connected} detail={integrations?.plaid.institutions.map((v) => v.name).join(", ")} onConnect={() => window.location.assign("/?workspace=expenses&connect=plaid")} />
          {integrations?.plaid.institutions.map((institution) => <div key={institution.id} className="flex items-center justify-between rounded-lg bg-slate-50 px-3 py-2 text-sm"><span>{institution.name}</span><Button size="sm" variant="outline" onClick={() => disconnect(`/api/integrations/plaid/${institution.id}`)}><Unplug className="h-4 w-4" />Disconnect</Button></div>)}
          <IntegrationRow name="Telegram" connected={integrations?.telegram.connected} onConnect={connectTelegram} onDisconnect={() => disconnect("/api/integrations/telegram")} />
          <IntegrationRow name="Splitwise" connected={integrations?.splitwise.connected} onConnect={connectSplitwise} onDisconnect={() => disconnect("/api/integrations/splitwise")} />
          <IntegrationRow name="Google Maps" connected detail="Application-managed" />
          <IntegrationRow name="OpenAI" connected detail="Application-managed" />
          {telegramCommand && <CopyValue label="Send this to the ExpenseOps Telegram bot" value={telegramCommand} />}
          {message && <p className="text-sm text-emerald-700">{message}</p>}
        </CardContent>
      </Card>

      <Card>
        <CardHeader><CardTitle>Workspaces</CardTitle><CardDescription>Switch among workspaces where you are a member.</CardDescription></CardHeader>
        <CardContent className="grid gap-3">
          {workspaces.map((workspace) => (
            <button key={workspace.id} type="button" onClick={() => !workspace.current && switchWorkspace(workspace.id)} className="flex items-center justify-between rounded-lg border border-slate-200 px-3 py-2 text-left hover:border-indigo-300 focus-visible:ring-2 focus-visible:ring-indigo-500">
              <span><span className="font-medium text-slate-950">{workspace.name}</span><span className="ml-2 text-xs text-slate-500">{workspace.role}</span></span>
              {workspace.current && <CheckCircle2 className="h-4 w-4 text-emerald-600" />}
            </button>
          ))}
          <div className="flex gap-2"><Input value={workspaceName} onChange={(event) => setWorkspaceName(event.target.value)} placeholder="New workspace name" /><Button disabled={!workspaceName.trim()} onClick={createWorkspace}><Plus className="h-4 w-4" />Create</Button></div>
          <div className="flex gap-2 border-t border-slate-200 pt-3">
            <Input aria-label="Current workspace name" value={renameValue} onChange={(event) => setRenameValue(event.target.value)} />
            <Button variant="outline" disabled={!renameValue.trim() || renameValue.trim() === context.workspace.name} onClick={renameWorkspace}><Pencil className="h-4 w-4" />Rename</Button>
          </div>
          <Button variant="outline" className="justify-self-start text-rose-700" onClick={leaveWorkspace}><UserMinus className="h-4 w-4" />Leave workspace</Button>
        </CardContent>
      </Card>

      <Card>
        <CardHeader><CardTitle>Members</CardTitle><CardDescription>Invite someone using a single-use, seven-day link.</CardDescription></CardHeader>
        <CardContent className="grid gap-3">
          <div className="grid gap-2">
            {members.map((member) => <div key={member.user_id} className="flex items-center justify-between rounded-lg border border-slate-200 px-3 py-2"><span><span className="font-medium text-slate-950">{member.display_name}</span><span className="ml-2 text-sm text-slate-600">{member.email}</span></span><span className="text-xs font-semibold uppercase tracking-wide text-slate-500">{member.role}</span></div>)}
          </div>
          <div className="flex gap-2"><Input value={inviteEmail} onChange={(event) => setInviteEmail(event.target.value)} placeholder="friend@example.com" /><Button disabled={!inviteEmail.includes("@")} onClick={invite}><Users className="h-4 w-4" />Invite</Button></div>
          {inviteLink && <CopyValue label="Share this invitation privately" value={inviteLink} />}
          <Button variant="outline" onClick={logout}><LogOut className="h-4 w-4" />Sign out</Button>
        </CardContent>
      </Card>
    </div>
  );
}

function ChecklistRow({ done, label, detail }: { done: boolean; label: string; detail: string }) {
  const Icon = done ? CheckCircle2 : Circle;
  return <div className="flex items-start gap-3 rounded-lg border border-slate-200 p-3"><Icon className={`mt-0.5 h-5 w-5 ${done ? "text-emerald-600" : "text-slate-400"}`} /><div><p className="font-medium text-slate-950">{label}</p><p className="text-sm text-slate-600">{detail}</p></div></div>;
}

function IntegrationRow({ name, connected, detail, onConnect, onDisconnect }: { name: string; connected?: boolean; detail?: string; onConnect?: () => void; onDisconnect?: () => void }) {
  return <div className="flex items-center justify-between gap-3 rounded-lg border border-slate-200 p-3"><div><p className="font-medium text-slate-950">{name}</p><p className={`text-sm ${connected ? "text-emerald-700" : "text-slate-500"}`}>{detail || (connected ? "Connected" : "Not connected")}</p></div>{onConnect && !connected ? <Button size="sm" onClick={onConnect}><Link2 className="h-4 w-4" />Connect</Button> : onDisconnect && connected ? <Button size="sm" variant="outline" onClick={onDisconnect}><Unplug className="h-4 w-4" />Disconnect</Button> : null}</div>;
}

function CopyValue({ label, value }: { label: string; value: string }) {
  return <div className="rounded-lg bg-slate-100 p-3"><p className="mb-1 text-xs font-semibold uppercase tracking-wide text-slate-600">{label}</p><div className="flex items-center gap-2"><code className="min-w-0 flex-1 truncate text-sm">{value}</code><Button size="sm" variant="outline" aria-label={`Copy ${label}`} onClick={() => navigator.clipboard.writeText(value)}><Copy className="h-4 w-4" /></Button></div></div>;
}
