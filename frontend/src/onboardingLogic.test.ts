import { describe, expect, it } from "vitest";

import { authenticationView, currentWorkspaceId, onboardingChecklist } from "./onboardingLogic";

describe("self-service onboarding UI state", () => {
  it("shows loading, sign-in, and authenticated app states deterministically", () => {
    expect(authenticationView(false, false)).toBe("loading");
    expect(authenticationView(true, false)).toBe("sign-in");
    expect(authenticationView(true, true)).toBe("app");
  });

  it("represents every optional integration without making it mandatory", () => {
    const rows = onboardingChecklist({
      gmail: { connected: true },
      plaid: { connected: false },
      telegram: { connected: false },
      splitwise: { connected: true },
    });
    expect(rows.map((row) => row.label)).toEqual(["Gmail", "Plaid", "Telegram", "Splitwise"]);
    expect(rows.filter((row) => row.done).map((row) => row.label)).toEqual(["Gmail", "Splitwise"]);
  });

  it("identifies the server-validated active workspace for the switcher", () => {
    expect(currentWorkspaceId([{ id: 1, current: false }, { id: 7, current: true }])).toBe(7);
    expect(currentWorkspaceId([])).toBeNull();
  });
});
