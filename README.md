# DealBot v1

**A voluntary, non-custodial deal ledger for livestream hosts and agencies operating across multiple platforms.**

Built by [Mountain View Provisions LLC](https://mountainviewprovisions.com)

---

## Overview

DealBot is a Discord bot that helps livestreaming agencies and hosts record, track, and verify deal agreements between parties across platforms like BigoLive, TikTok, Tango, YouTube, and Twitch.

It does **not** move money, enforce agreements legally, or affiliate with any platform. It is a transparent ledger — a shared, auditable record of what was agreed, by whom, and what happened.

### What DealBot tracks

- Deal creation with both-party confirmation
- Completion with dual-confirmation (both parties must confirm)
- Disputes with admin/mediator resolution
- Overdue detection with automatic DM reminders
- Weighted reputation scores per platform network
- Full immutable audit trail for every status change
- Per-server configurable rate limits

---

## Supported Networks

| Network | Identifier |
|---------|------------|
| BigoLive | `BigoLive` |
| TikTok | `TikTok` |
| Tango | `Tango` |
| YouTube | `YouTube` |
| Twitch | `Twitch` |
| Other | `Other` |

---

## Deal Lifecycle

```
PENDING_CONFIRMATION → ACTIVE → PENDING_COMPLETION → COMPLETED
                              ↘ DISPUTED → ACTIVE / COMPLETED / DEFAULTED
                              ↘ OVERDUE  → PENDING_COMPLETION / COMPLETED / DISPUTED / DEFAULTED
                     ↘ CANCELLED
```

| Status | Meaning |
|--------|---------|
| `pending_confirmation` 🟡 | Deal created, awaiting both-party confirmation |
| `active` 🟢 | Both parties confirmed, deal in progress |
| `pending_completion` 🔵 | One party marked complete, awaiting the other |
| `completed` ✅ | Both parties confirmed completion |
| `disputed` 🔴 | Flagged as disputed, reputation updates frozen |
| `overdue` 🟠 | Past due date, not yet resolved |
| `defaulted` ⛔ | Admin-set terminal status for failed deals |
| `cancelled` ⚫ | Cancelled before activation |

---

## Commands

### `/deal` — Deal Management

#### `/deal start <counterparty>`
Opens a private DM wizard to create a new deal. The wizard walks the initiator through 6 steps:
1. Select the network (BigoLive, TikTok, etc.)
2. Your platform username on that network
3. Counterparty's platform username
4. Deal type (hosting, gifting, promotion, collab, other)
5. Amount and currency (e.g. `500 USD` or `1000 diamonds`)
6. Due date (YYYY-MM-DD format)

After a summary confirmation, the deal is created with status `pending_confirmation`. The counterparty receives an automatic DM notification.

**Parameters:**
- `counterparty` — The Discord user you're dealing with (required)

**Restrictions:** Cannot deal with yourself or a bot. Rate limited by open deal count and daily volume.

---

#### `/deal confirm <deal_id>`
Confirms a pending deal. Both parties must call this before the deal becomes `active`.

**Parameters:**
- `deal_id` — 8-character deal ID (e.g. `3F9A2B01`)

---

#### `/deal complete <deal_id>`
Signals that your side of the deal is done. Once both parties confirm, the deal moves to `completed`. Works from `active` or `overdue` status.

**Parameters:**
- `deal_id` — 8-character deal ID

---

#### `/deal dispute <deal_id> <reason>`
Flags a deal as disputed. Freezes reputation score updates until an admin resolves it. Subject to dispute cooldown per server config.

**Parameters:**
- `deal_id` — 8-character deal ID
- `reason` — Description of the dispute (10–500 characters)

---

#### `/deal note <deal_id> <note>`
Appends an immutable note to a deal. Notes are permanent — they cannot be edited or deleted. Only parties to the deal may add notes.

**Parameters:**
- `deal_id` — 8-character deal ID
- `note` — Note text (3–1000 characters)

---

#### `/deal view <deal_id> [audit]`
Displays full deal details including parties, status, amounts, confirmation flags, and notes. Optionally includes the full audit trail.

**Parameters:**
- `deal_id` — 8-character deal ID
- `audit` — `true` to include the audit log (default: `false`)

---

#### `/deal list [status] [network]`
Lists all your deals with pagination (8 per page). Supports optional filters.

**Parameters:**
- `status` — Filter by status string (e.g. `active`, `completed`, `disputed`)
- `network` — Filter by network name (e.g. `TikTok`)

---

#### `/deal search <deal_id>`
Finds and displays a specific deal by ID. Must be a party to the deal.

**Parameters:**
- `deal_id` — 8-character deal ID

---

### `/rep` — Reputation

#### `/rep <user> [network]`
Displays a user's weighted reputation score and deal history. Broken down by platform network with a global aggregate.

**Score tiers:**
| Score | Tier |
|-------|------|
| 750–1000 | 🌟 Excellent |
| 600–749 | ✅ Good |
| 400–599 | 🟡 Fair |
| 200–399 | 🟠 Poor |
| 0–199 | 🔴 Very Poor |

**Parameters:**
- `user` — The Discord member to look up (required)
- `network` — Optional: filter to a specific network (autocomplete supported)

**Scoring formula:** Each deal contributes a signed weighted score based on recency (half-life: 180 days) and volume (log-scaled). Completed = positive, defaulted/overdue = negative, disputed = excluded.

---

### `/rate_limits` — Server Configuration

#### `/rate_limits view`
Shows the current rate limit settings for this server.

---

#### `/rate_limits set [options]`
**Admin only.** Configures server-specific rate limits. All parameters are optional — omit any to keep the current value.

**Parameters:**
| Parameter | Default | Description |
|-----------|---------|-------------|
| `max_open` | 10 | Max open deals per user |
| `max_volume` | 50,000 | Max daily deal volume per user |
| `cooldown_hours` | 24 | Hours between disputes per user |
| `reminder_mins` | 30 | Reminder loop frequency in minutes |
| `weekly_summary` | true | Enable weekly digest DMs |
| `mediator_role` | DealMediator | Role name with mediator permissions |

---

### `/admin` — Admin & Mediator Commands

Requires **Administrator** Discord permission or the configured mediator role.

#### `/admin resolve <deal_id> <resolution> [note]`
Resolves a disputed deal by setting it to a new status.

**Parameters:**
- `deal_id` — 8-character deal ID
- `resolution` — `active`, `completed`, or `defaulted`
- `note` — Optional admin note recorded in the audit log

---

#### `/admin export`
Exports all deals visible to the requesting admin as a CSV file attachment.

---

#### `/admin backup`
Triggers an immediate SQLite database backup. Backup is stored in the configured `BACKUP_DIR`.

---

#### `/admin stats [days]`
Displays command usage statistics for the server over the last N days.

**Parameters:**
- `days` — Look-back period in days (default: 30)

---

### `/about`
Shows a public information embed explaining what DealBot does, what it doesn't do, its data policy, and how cross-network reputation works.

---

### `/export_data`
Downloads all your personal deal data as a CSV (GDPR-style export). Includes all deals you were party to and all notes you authored.

---

## DM Reminders

DealBot sends automatic DMs for:

| Trigger | Message |
|---------|---------|
| Deal due in ≤48 hours | ⏰ Advance warning |
| Deal due in ≤24 hours | 🔶 Urgent escalation |
| Deal becomes overdue | 🔴 Overdue notification |
| Weekly summary | 📋 Digest of all open deals (if enabled) |

Reminders run on a configurable loop (default: every 30 minutes). Weekly summaries fire on Sundays at 00:00 UTC. Users with DMs disabled will not receive reminders — their deals are still tracked normally.

---

## Rate Limiting

DealBot enforces per-user limits to prevent abuse:

- **Open deal cap** — Cannot create a new deal if you already have N open deals
- **Daily volume cap** — Total deal value created in a calendar day is capped
- **Dispute cooldown** — Must wait N hours between disputes

Limits are configurable per server by admins. Global environment variable fallbacks apply when no server config exists.

---

## Data & Privacy

- All data is stored in a local SQLite database on the bot's host server
- No data is sent to third-party services
- Users can export all their personal data at any time via `/export_data`
- The audit log is append-only and tamper-evident by design
- Backups are performed daily at 02:00 UTC and optionally encrypted with AES-256-GCM

---

## Setup & Configuration

### Requirements

- Python 3.11+
- `discord.py 2.3.2`
- `aiosqlite`
- `python-dotenv`
- `cryptography` (optional, for encrypted backups)

### Installation

```bash
git clone <repo>
cd dealbot_v1
pip install -r requirements.txt
cp .env.example .env
# Edit .env with your values
python main.py
```

### Environment Variables

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `DISCORD_TOKEN` | ✅ | — | Your bot token from Discord Developer Portal |
| `DISCORD_GUILD_ID` | — | — | Guild ID for instant command sync (omit for global) |
| `DATABASE_PATH` | — | `./dealbot.db` | Path to SQLite database file |
| `LOG_LEVEL` | — | `INFO` | Logging level (`DEBUG`, `INFO`, `WARNING`, `ERROR`) |
| `LOG_CHANNEL_ID` | — | — | Discord channel ID to mirror ERROR+ logs |
| `BOT_OWNER_IDS` | — | — | Comma-separated Discord user IDs with owner privileges |
| `MEDIATOR_ROLE_NAME` | — | `DealMediator` | Server role name that grants admin command access |
| `BACKUP_DIR` | — | `./backups` | Directory for database backups |
| `BACKUP_ENCRYPTION_KEY` | — | — | Passphrase for AES-256-GCM backup encryption |
| `REMINDER_LOOP_MINUTES` | — | `30` | How often the reminder task runs |
| `WEEKLY_SUMMARY_ENABLED` | — | `true` | Enable weekly deal digest DMs |
| `MAX_OPEN_DEALS_PER_USER` | — | `10` | Global default open deal limit |
| `MAX_DAILY_DEAL_VOLUME` | — | `50000` | Global default daily volume limit |
| `DISPUTE_COOLDOWN_HOURS` | — | `24` | Global default dispute cooldown |

---

## Architecture

```
dealbot_v1/
├── main.py                        # Entry point, bot instance, event handlers
├── bot/
│   ├── commands/                  # Discord slash command cogs
│   │   ├── deal.py                # /deal group
│   │   ├── reputation.py          # /rep command
│   │   ├── rate_limits.py         # /rate_limits group
│   │   ├── admin.py               # /admin group
│   │   └── misc.py                # /about, /export_data
│   ├── services/                  # Business logic layer
│   │   ├── deal_service.py        # Deal lifecycle & state machine
│   │   ├── audit_service.py       # Audit log writes
│   │   ├── reminder_service.py    # DM notifications
│   │   ├── reputation_service.py  # Weighted score calculation
│   │   ├── rate_limit_service.py  # Rate limit enforcement
│   │   └── backup_service.py      # Backup & CSV export
│   ├── database/
│   │   ├── sqlite.py              # Connection management
│   │   ├── migrations.py          # Schema DDL (idempotent)
│   │   └── queries.py             # All SQL queries
│   ├── models/
│   │   ├── enums.py               # DealStatus, DealType, ActionType, VALID_TRANSITIONS
│   │   └── dataclasses.py         # Typed DTOs (DealModel, GlobalReputation, etc.)
│   ├── tasks/
│   │   └── reminder_loop.py       # Background task coroutines
│   └── utils/
│       ├── embeds.py              # Discord embed factories
│       ├── wizard.py              # DM deal creation wizard
│       ├── pagination.py          # Paginator UI view
│       ├── validators.py          # Input validation helpers
│       ├── id_generator.py        # 8-char deal ID generation
│       └── logger.py              # Logging setup + Discord channel handler
└── requirements.txt
```

---

## Disclaimer

DealBot records voluntary agreements. It does not:
- Handle, hold, or move any money or digital assets
- Enforce deals legally or financially
- Determine fraud — only records objective outcomes
- Represent, affiliate with, or endorse BigoLive, TikTok, Tango, YouTube, Twitch, or any other platform

Use this tool responsibly and in good faith.

---

*DealBot v1 — Built by [Mountain View Provisions LLC](https://mountainviewprovisions.com)*
