export type IntegrationReadiness = {
  gmail: { connected: boolean };
  plaid: { connected: boolean };
  telegram: { connected: boolean };
  splitwise: { connected: boolean };
};

export function authenticationView(authResolved: boolean, hasContext: boolean) {
  if (!authResolved) return "loading";
  return hasContext ? "app" : "sign-in";
}

export function onboardingChecklist(integrations: IntegrationReadiness) {
  return [
    { label: "Gmail", done: integrations.gmail.connected, detail: "Receipts and Promotion Intelligence" },
    { label: "Plaid", done: integrations.plaid.connected, detail: "Transactions and expense workflows" },
    { label: "Telegram", done: integrations.telegram.connected, detail: "Mobile approvals and receipt uploads" },
    { label: "Splitwise", done: integrations.splitwise.connected, detail: "Shared expense posting" },
  ];
}

export function currentWorkspaceId(workspaces: { id: number; current: boolean }[]) {
  return workspaces.find((workspace) => workspace.current)?.id ?? null;
}
