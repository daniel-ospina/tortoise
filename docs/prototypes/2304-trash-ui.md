# Prototype — #2304 Trash UI (dashboard, Graphs tab)

> Status: **AWAITING OWNER APPROVAL** (UI + copy). Markdown prototype per owner
> request. Backend (trash list / restore / rescue / purge) is implemented and
> merged into this branch (`feat/2304-delete-trash`). Live surface today:
> `website/apps/dashboard/src/main.jsx` Graphs tab (inline row delete-confirm).
>
> **Semantics this UI expresses:** delete = move to Trash → recoverable **7
> days** → then permanently erased (graph data + all backups). Keys are
> revoked immediately at delete and stay dead — restoring never brings keys
> back.

---

## 1. Where it lives

The existing **Graphs** tab gains a second section under the active graph
table, shown **only to owners/admins** (same visibility as the Delete button
today) and **only when the trash has ≥1 item**:

```
Graphs
┌──────────────────────────────────────────────────────────────┐
│ [+ Create]                                  used 1 · cap 2  │
│ Name        Kind     Status   Keys                  Actions │
│ research    custom   active   2          [Keys] [Delete]   │
│ default     default  active   —          [Keys]            │
├──────────────────────────────────────────────────────────────┤
│ 🗑 Trash (1) — graphs here are kept 7 days, then erased      │
│ Name        Deleted        Erases in              Actions   │
│ old-bot     Sep 4, 14:32   3 days        [Restore] [Inspect]│
└──────────────────────────────────────────────────────────────┘
```

- The trash section is a **collapsed-by-default `<details>`** when ≥1 item
  ("🗑 Trash (1)"). If the team has items, a subtle one-time count badge is
  fine — no toast spam.
- Row actions: **Restore** and **Inspect** (rescue read-only view).
- Purged rows never appear (nothing to restore) — backend already filters.

---

## 2. Restore flow

**Plain restore (no name conflict):**

```
Restore "old-bot"?
  This brings the graph back as active with its data intact.
  • Its API keys stay revoked — you'll mint fresh keys after restoring.
  • Available: <X> days left of the 7-day recovery window.
                 [Cancel]   [Restore graph]
```

On success, the graph row re-appears in the active table; the trash section
updates. A one-line note shows under the table: *"old-bot restored. Mint fresh
keys for it under Keys."*

**Name conflict (a live graph now uses the name "old-bot"):**

```
Restore "old-bot"?
  ⚠ A live graph is already named "old-bot".
  Two active graphs can't share a name, so restore is paused.
  Rename or delete the live "old-bot" first, then restore again.
                 [Done]
```
(No auto-rename in v1 — keep it deterministic; rename is a future item.)

**Purged (raced the 7-day clock):** restore is refused by the server (410);
the UI simply won't list it. If a stale tab tries, show the server message:
*"This graph was permanently erased after its recovery window."*

---

## 3. Inspect (read-only rescue view)

Opens a small panel (reuse the existing Keys-panel pattern) — **read-only**,
no way to write or export from here:

```
Inspect "old-bot"                [Close]
  Deleted:   Sep 4, 14:32 · erases in 3 days
  Backups kept: 4
  Latest backup: Sep 4, 14:00 · 1,204 nodes / 9,882 edges
  What can I do?
    • Restore brings all of this back as an active graph.
    • After the 7 days, the graph and its backups are permanently erased.
```

---

## 4. Delete-confirm (CHANGED copy — today's text is now wrong)

Today the inline confirm says **"Data and keys are removed permanently."** —
no longer true. Proposed replacement (inline, same pattern):

> **Delete "old-bot"?** Keys are revoked now. The graph goes to Trash, where
> you can restore it for **7 days** — then it and its backups are permanently
> erased.  `[Delete]  [Cancel]`

Longer variant (if inline space allows a modal later — not in v1):

> **Delete "old-bot"?**
> This moves the graph to your team's Trash.
> - API keys are revoked immediately (permanent — you'll mint new ones if you
>   ever restore).
> - You can restore the graph for the next **7 days**.
> - After 7 days it is permanently erased — the graph data **and its backup
>   copies**.
> `[Cancel]  [Move to Trash]`

---

## 5. Copy blocks — FOR APPROVAL

### 5a. Delete confirm (inline) — EN
"Delete \"{name}\"? Keys are revoked now. The graph goes to Trash, where you can restore it for 7 days — then it and its backups are permanently erased."

### 5b. Trash section heading — EN
"Trash (N) — deleted graphs are kept 7 days, then permanently erased"

### 5c. Restore modal title/body — EN
"Restore \"{name}\"?" / "This brings the graph back as active with its data intact. Its API keys stay revoked — you'll mint fresh keys after restoring. Available: {n} days left of the 7-day recovery window."

### 5d. Name-conflict notice — EN
"Two active graphs can't share a name. Rename or delete the live \"{name}\" first, then restore again."

### 5e. Rescue panel — EN
Heading "Inspect \"{name}\""; body rows "Deleted: {date} · erases in {n} days", "Backups kept: {n}", "Latest backup: {date} · {nodes} nodes / {edges} edges"; footnote "After 7 days, the graph and its backups are permanently erased."

### 5f. Success note after restore — EN
"\"{name}\" restored. Mint fresh keys for it under Keys."

### 5g. Erasure/410 — EN
"This graph was permanently erased after its recovery window."

### 5h. Privacy-page copy (draft, website/privacy.html §6/§16 touch) — EN
Replace permanent-delete phrasing with:
- "Deleting a knowledge graph moves it to a 7-day recovery window (Trash). During that window you can restore it; API keys are revoked immediately. After 7 days the graph is permanently erased, including any stored backup copies. We do not keep deleted graphs beyond the recovery window except as required by law or for fraud/security investigations."
(Owner decision recorded: GDPR basis = disclosed contractual window + restore-time re-erasure; counsel review of §16 remains the mandatory gate.)

---

## 6. Open product questions (owner to confirm)

1. **7-day window** — keep as scoped? (Easy to change to 14/30 before ship.)
2. **Spanish localization** — copy must ship in ES too (dashboard is ES/EN)?
   If yes I'll produce the ES block for review in the same pass.
3. **Collapsed-by-default trash section** vs a visible table — preference?
4. **"Inspect" naming** — alternatives: "What's inside" / "Details" / "Rescue".

## 7. What happens after approval

- Apply the UI (graphs.js data loading + main.jsx section/modal/copy) + tests.
- Apply the privacy-page copy.
- Docker-lane E2E (delete → trash → inspect → restore → delete → purge →
  restore-refused), then full review + merge.
