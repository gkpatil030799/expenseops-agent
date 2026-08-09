import { useEffect, useMemo, useState } from "react";
import {
  AlertCircle,
  CheckCircle2,
  Copy,
  MailPlus,
  Plus,
  RefreshCw,
  Send,
  UserMinus,
  UserPlus,
  UsersRound,
} from "lucide-react";

import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { api } from "@/lib/api";
import type { Friend, Group } from "@/types";

type GroupType = "home" | "trip" | "couple" | "other";

export function GroupManagementPanel({ currentUserId }: { currentUserId: number | null }) {
  const [groups, setGroups] = useState<Group[]>([]);
  const [friends, setFriends] = useState<Friend[]>([]);
  const [selectedGroupId, setSelectedGroupId] = useState<number | null>(null);
  const [members, setMembers] = useState<Friend[]>([]);
  const [newGroupName, setNewGroupName] = useState("");
  const [newGroupType, setNewGroupType] = useState<GroupType>("home");
  const [simplifyByDefault, setSimplifyByDefault] = useState(false);
  const [newGroupUserIds, setNewGroupUserIds] = useState<number[]>([]);
  const [friendFilter, setFriendFilter] = useState("");
  const [memberToAdd, setMemberToAdd] = useState("");
  const [inviteFirstName, setInviteFirstName] = useState("");
  const [inviteLastName, setInviteLastName] = useState("");
  const [inviteEmail, setInviteEmail] = useState("");
  const [busy, setBusy] = useState<string | null>(null);
  const [notice, setNotice] = useState<{ tone: "success" | "error"; text: string } | null>(null);

  const visibleFriends = useMemo(() => {
    const query = friendFilter.trim().toLowerCase();
    if (!query) return friends;
    return friends.filter((friend) =>
      `${friend.display_name} ${friend.email || ""}`.toLowerCase().includes(query),
    );
  }, [friendFilter, friends]);

  const availableMembers = useMemo(() => {
    const memberIds = new Set(members.map((member) => member.id));
    return friends.filter((friend) => !memberIds.has(friend.id));
  }, [friends, members]);
  const selectedGroup = groups.find((group) => group.id === selectedGroupId) ?? null;

  useEffect(() => {
    void loadDirectory();
  }, []);

  useEffect(() => {
    if (selectedGroupId === null) {
      setMembers([]);
      return;
    }
    void loadMembers(selectedGroupId);
  }, [selectedGroupId]);

  async function loadDirectory() {
    setBusy("refresh");
    setNotice(null);
    try {
      const [loadedGroups, loadedFriends] = await Promise.all([
        api<Group[]>("/splitwise/groups"),
        api<Friend[]>("/splitwise/friends"),
      ]);
      const manageableGroups = loadedGroups.filter((group) => group.id !== 0);
      setGroups(manageableGroups);
      setFriends(loadedFriends);
      setSelectedGroupId((current) =>
        current !== null && manageableGroups.some((group) => group.id === current)
          ? current
          : manageableGroups[0]?.id ?? null,
      );
    } catch (error) {
      setNotice({ tone: "error", text: errorMessage(error) });
    } finally {
      setBusy(null);
    }
  }

  async function loadMembers(groupId: number) {
    setBusy("members");
    try {
      const [group, loadedMembers] = await Promise.all([
        api<Group>(`/splitwise/groups/${groupId}`),
        api<Friend[]>(`/splitwise/groups/${groupId}/members`),
      ]);
      setGroups((current) => current.map((item) => (item.id === group.id ? group : item)));
      setMembers(loadedMembers);
      setMemberToAdd("");
    } catch (error) {
      setNotice({ tone: "error", text: errorMessage(error) });
    } finally {
      setBusy(null);
    }
  }

  function toggleNewGroupFriend(userId: number) {
    setNewGroupUserIds((current) =>
      current.includes(userId) ? current.filter((id) => id !== userId) : [...current, userId],
    );
  }

  async function createGroup() {
    const name = newGroupName.trim();
    if (!name) {
      setNotice({ tone: "error", text: "Enter a group name." });
      return;
    }
    setBusy("create");
    setNotice(null);
    try {
      const created = await api<Group>("/splitwise/groups", {
        method: "POST",
        body: JSON.stringify({
          name,
          group_type: newGroupType,
          simplify_by_default: simplifyByDefault,
          user_ids: newGroupUserIds,
        }),
      });
      setGroups((current) => [created, ...current.filter((group) => group.id !== created.id)]);
      setSelectedGroupId(created.id);
      setNewGroupName("");
      setNewGroupUserIds([]);
      setNotice({ tone: "success", text: `${created.name} was created in Splitwise.` });
    } catch (error) {
      setNotice({ tone: "error", text: errorMessage(error) });
    } finally {
      setBusy(null);
    }
  }

  async function addMember() {
    if (selectedGroupId === null || !memberToAdd) return;
    setBusy("add");
    setNotice(null);
    try {
      const updated = await api<Friend[]>(`/splitwise/groups/${selectedGroupId}/members`, {
        method: "POST",
        body: JSON.stringify({ user_id: Number(memberToAdd) }),
      });
      setMembers(updated);
      setMemberToAdd("");
      setNotice({ tone: "success", text: "Participant added to the Splitwise group." });
    } catch (error) {
      setNotice({ tone: "error", text: errorMessage(error) });
    } finally {
      setBusy(null);
    }
  }

  async function inviteMember() {
    if (selectedGroupId === null) return;
    if (!inviteFirstName.trim() || !inviteEmail.trim()) {
      setNotice({ tone: "error", text: "Enter the participant's first name and email." });
      return;
    }
    setBusy("invite");
    setNotice(null);
    try {
      const updated = await api<Friend[]>(`/splitwise/groups/${selectedGroupId}/invite`, {
        method: "POST",
        body: JSON.stringify({
          first_name: inviteFirstName,
          last_name: inviteLastName,
          email: inviteEmail,
        }),
      });
      setMembers(updated);
      setInviteFirstName("");
      setInviteLastName("");
      setInviteEmail("");
      setNotice({
        tone: "success",
        text: "Splitwise invitation sent. The participant can be included before accepting it.",
      });
    } catch (error) {
      setNotice({ tone: "error", text: errorMessage(error) });
    } finally {
      setBusy(null);
    }
  }

  async function copyInviteLink() {
    if (!selectedGroup?.invite_link) return;
    try {
      await navigator.clipboard.writeText(selectedGroup.invite_link);
      setNotice({ tone: "success", text: "Group invitation link copied to your clipboard." });
    } catch {
      setNotice({
        tone: "error",
        text: "The browser blocked clipboard access. Open the link and copy it from the address bar.",
      });
    }
  }

  async function removeMember(member: Friend) {
    if (selectedGroupId === null || member.id === currentUserId) return;
    if (!window.confirm(`Remove ${member.display_name} from this Splitwise group?`)) return;
    setBusy(`remove-${member.id}`);
    setNotice(null);
    try {
      const updated = await api<Friend[]>(
        `/splitwise/groups/${selectedGroupId}/members/${member.id}`,
        { method: "DELETE" },
      );
      setMembers(updated);
      setNotice({ tone: "success", text: `${member.display_name} was removed.` });
    } catch (error) {
      setNotice({
        tone: "error",
        text: `${errorMessage(error)} Splitwise requires the participant's group balance to be zero before removal.`,
      });
    } finally {
      setBusy(null);
    }
  }

  return (
    <Card>
      <CardHeader className="flex-row items-start justify-between gap-3 space-y-0">
        <div>
          <div className="mb-1 inline-flex items-center gap-2 text-xs font-semibold uppercase text-slate-500">
            <UsersRound className="h-3.5 w-3.5" />
            Splitwise groups
          </div>
          <CardTitle className="text-lg">Manage groups and participants</CardTitle>
          <CardDescription className="mt-1">
            Create a group, add existing Splitwise friends, or remove settled participants.
          </CardDescription>
        </div>
        <Button variant="outline" size="sm" onClick={loadDirectory} disabled={busy !== null}>
          <RefreshCw className={`h-4 w-4 ${busy === "refresh" ? "animate-spin" : ""}`} />
          Refresh
        </Button>
      </CardHeader>
      <CardContent className="space-y-4">
        {notice ? (
          <div
            className={`flex items-start gap-2 rounded-md border px-3 py-2 text-sm ${
              notice.tone === "success"
                ? "border-teal-200 bg-teal-50 text-teal-800"
                : "border-rose-200 bg-rose-50 text-rose-800"
            }`}
          >
            {notice.tone === "success" ? (
              <CheckCircle2 className="mt-0.5 h-4 w-4 shrink-0" />
            ) : (
              <AlertCircle className="mt-0.5 h-4 w-4 shrink-0" />
            )}
            {notice.text}
          </div>
        ) : null}

        <div className="grid gap-5 xl:grid-cols-2">
          <section className="space-y-3 rounded-lg border border-slate-200 bg-slate-50/60 p-4">
            <div>
              <h3 className="font-semibold text-slate-950">Create a new group</h3>
              <p className="text-xs text-slate-500">You are included automatically by Splitwise.</p>
            </div>
            <div className="grid gap-2 sm:grid-cols-[minmax(0,1fr)_150px]">
              <Input
                value={newGroupName}
                onChange={(event) => setNewGroupName(event.target.value)}
                placeholder="Group name"
                maxLength={255}
              />
              <select
                className="h-9 rounded-md border border-slate-200 bg-white px-3 text-sm text-slate-800 shadow-sm outline-none focus:ring-2 focus:ring-indigo-500"
                value={newGroupType}
                onChange={(event) => setNewGroupType(event.target.value as GroupType)}
              >
                <option value="home">Home</option>
                <option value="trip">Trip</option>
                <option value="couple">Couple</option>
                <option value="other">Other</option>
              </select>
            </div>
            <Input
              value={friendFilter}
              onChange={(event) => setFriendFilter(event.target.value)}
              placeholder="Filter friends to include"
            />
            <div className="max-h-44 space-y-1 overflow-y-auto rounded-md border border-slate-200 bg-white p-2">
              {visibleFriends.length ? (
                visibleFriends.map((friend) => (
                  <label
                    key={friend.id}
                    className="flex cursor-pointer items-center gap-2 rounded px-2 py-1.5 text-sm hover:bg-slate-50"
                  >
                    <input
                      type="checkbox"
                      checked={newGroupUserIds.includes(friend.id)}
                      onChange={() => toggleNewGroupFriend(friend.id)}
                      className="h-4 w-4 rounded border-slate-300 text-indigo-600"
                    />
                    <span className="min-w-0">
                      <span className="block truncate font-medium text-slate-800">{friend.display_name}</span>
                      {friend.email ? <span className="block truncate text-xs text-slate-500">{friend.email}</span> : null}
                    </span>
                  </label>
                ))
              ) : (
                <p className="p-2 text-xs text-slate-500">No matching Splitwise friends.</p>
              )}
            </div>
            <div className="flex items-center justify-between gap-3">
              <label className="inline-flex items-center gap-2 text-xs font-medium text-slate-600">
                <input
                  type="checkbox"
                  checked={simplifyByDefault}
                  onChange={(event) => setSimplifyByDefault(event.target.checked)}
                  className="h-4 w-4 rounded border-slate-300 text-indigo-600"
                />
                Simplify group debts
              </label>
              <Button onClick={createGroup} disabled={busy !== null || !newGroupName.trim()}>
                <Plus className="h-4 w-4" />
                Create group
              </Button>
            </div>
          </section>

          <section className="space-y-3 rounded-lg border border-slate-200 bg-slate-50/60 p-4">
            <div>
              <h3 className="font-semibold text-slate-950">Existing group</h3>
              <p className="text-xs text-slate-500">Select a group to manage its participants.</p>
            </div>
            <select
              className="h-9 w-full rounded-md border border-slate-200 bg-white px-3 text-sm text-slate-800 shadow-sm outline-none focus:ring-2 focus:ring-indigo-500"
              value={selectedGroupId ?? ""}
              onChange={(event) => setSelectedGroupId(event.target.value ? Number(event.target.value) : null)}
              disabled={!groups.length}
            >
              {!groups.length ? <option value="">No Splitwise groups found</option> : null}
              {groups.map((group) => <option key={group.id} value={group.id}>{group.name}</option>)}
            </select>
            <div className="flex gap-2">
              <select
                className="h-9 min-w-0 flex-1 rounded-md border border-slate-200 bg-white px-3 text-sm text-slate-800 shadow-sm outline-none focus:ring-2 focus:ring-indigo-500"
                value={memberToAdd}
                onChange={(event) => setMemberToAdd(event.target.value)}
                disabled={selectedGroupId === null || !availableMembers.length}
              >
                <option value="">Choose a friend to add</option>
                {availableMembers.map((friend) => <option key={friend.id} value={friend.id}>{friend.display_name}</option>)}
              </select>
              <Button variant="outline" onClick={addMember} disabled={busy !== null || !memberToAdd}>
                <UserPlus className="h-4 w-4" />
                Add
              </Button>
            </div>
            <div className="space-y-2 rounded-md border border-indigo-100 bg-indigo-50/60 p-3">
              <div>
                <h4 className="text-sm font-semibold text-indigo-950">Invite someone new</h4>
                <p className="text-xs text-indigo-700">
                  Splitwise will email an invitation to someone who does not have an account yet.
                </p>
              </div>
              <div className="grid gap-2 sm:grid-cols-2">
                <Input
                  value={inviteFirstName}
                  onChange={(event) => setInviteFirstName(event.target.value)}
                  placeholder="First name"
                />
                <Input
                  value={inviteLastName}
                  onChange={(event) => setInviteLastName(event.target.value)}
                  placeholder="Last name (optional)"
                />
              </div>
              <div className="flex flex-col gap-2 sm:flex-row">
                <Input
                  className="min-w-0 flex-1"
                  type="email"
                  value={inviteEmail}
                  onChange={(event) => setInviteEmail(event.target.value)}
                  placeholder="Email address"
                />
                <Button
                  onClick={inviteMember}
                  disabled={
                    busy !== null ||
                    selectedGroupId === null ||
                    !inviteFirstName.trim() ||
                    !inviteEmail.trim()
                  }
                >
                  <MailPlus className="h-4 w-4" />
                  Send invite
                </Button>
              </div>
            </div>
            <div className="space-y-2 rounded-md border border-slate-200 bg-white p-3">
              <div>
                <h4 className="text-sm font-semibold text-slate-900">Group invitation link</h4>
                <p className="truncate text-xs text-slate-500">
                  {selectedGroup?.invite_link || "No invitation link returned by Splitwise."}
                </p>
              </div>
              <div className="flex flex-wrap gap-2">
                <Button
                  variant="outline"
                  size="sm"
                  onClick={copyInviteLink}
                  disabled={!selectedGroup?.invite_link}
                >
                  <Copy className="h-4 w-4" />
                  Copy link
                </Button>
                {selectedGroup?.invite_link ? (
                  <Button asChild variant="outline" size="sm">
                    <a
                      href={`https://t.me/share/url?url=${encodeURIComponent(selectedGroup.invite_link)}&text=${encodeURIComponent(`Join my ${selectedGroup.name} group on Splitwise`)}`}
                      target="_blank"
                      rel="noreferrer"
                    >
                      <Send className="h-4 w-4" />
                      Share via Telegram
                    </a>
                  </Button>
                ) : null}
              </div>
            </div>
            <div className="max-h-56 space-y-1 overflow-y-auto rounded-md border border-slate-200 bg-white p-2">
              {busy === "members" ? (
                <p className="p-2 text-xs text-slate-500">Loading participants…</p>
              ) : members.length ? (
                members.map((member) => {
                  const isCurrentUser = member.id === currentUserId;
                  return (
                    <div key={member.id} className="flex items-center justify-between gap-3 rounded px-2 py-1.5 hover:bg-slate-50">
                      <div className="min-w-0 text-sm">
                        <span className="block truncate font-medium text-slate-800">
                          {member.display_name}{isCurrentUser ? " (you)" : ""}
                        </span>
                        <span className="block truncate text-xs text-slate-500">
                          {member.email || `Splitwise ID ${member.id}`}
                          {member.registration_status && member.registration_status !== "confirmed"
                            ? ` · ${member.registration_status} invitation`
                            : ""}
                        </span>
                      </div>
                      <Button
                        variant="ghost"
                        size="sm"
                        className="text-rose-700 hover:bg-rose-50 hover:text-rose-800"
                        onClick={() => removeMember(member)}
                        disabled={busy !== null || isCurrentUser}
                        title={isCurrentUser ? "Your own membership cannot be removed here" : "Remove participant"}
                      >
                        <UserMinus className="h-4 w-4" />
                        Remove
                      </Button>
                    </div>
                  );
                })
              ) : (
                <p className="p-2 text-xs text-slate-500">No participants found for this group.</p>
              )}
            </div>
          </section>
        </div>
      </CardContent>
    </Card>
  );
}

function errorMessage(error: unknown) {
  if (typeof error === "string") return error;
  if (error && typeof error === "object") {
    const detail = (error as { detail?: unknown }).detail;
    if (typeof detail === "string") return detail;
  }
  return "The Splitwise operation failed. Try refreshing and retrying.";
}
