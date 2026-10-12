# Nations notification-service feed

One read-only WebSocket supplies registered usernames, unfinished-game metadata,
and pending turn owners. External notification services can replace public
webpage scraping and per-match sockets entirely. Subscriptions, Discord accounts,
channels, deduplication and delivery preferences stay in the external service.

## Deployment

Set `NATIONS_TURN_FEED_ENABLED=TRUE` in the ASGI process environment and restart
the application. The endpoint is disabled by default. Multi-process production
deployments require the existing shared Redis channel layer; the in-memory layer
only works within one process. No new dependency or database migration is needed.

Connect to `wss://games.tabony.net/ws/nations/turns/`. This anonymous endpoint
publishes game metadata and an aggregate registered-username directory, including
accounts with no ongoing games. The operator must opt in to publishing this
directory. Only account IDs and exact usernames are exposed: no email addresses,
real names, passwords, sessions, notes, chats or notification settings.

## Initial snapshot

The server immediately sends all registered usernames except the internal
`deleted_player` placeholder and all unfinished games. There is no seven-day
cutoff: archived ongoing games and waiting games are included.

```json
{
  "type": "snapshot",
  "players": [{"id": "17", "name": "Kayser"}],
  "games": [{
    "game": "3038",
    "title": "S11-1A-P2",
    "players": ["Kayser", "Suddoman"],
    "invited": [],
    "player_count": 2,
    "started": true,
    "player": "Kayser"
  }]
}
```

Replace both client caches with the snapshot. All IDs are strings; usernames
retain case. `players` on a game contains its recorded participants, including
invitations, in existing player order. `invited` lists unaccepted invitations.
`player_count` is the game's capacity. `started` indicates a nonempty saved replay;
`player` is the pending owner, or `null` for a waiting game or deleted-player
placeholder. Do not issue turn reminders for unstarted games.

The player directory supplies typeahead even before an account joins a game.
Stable account IDs let the client replace names reliably after a rename.

## Incremental events

Registrations and account renames upsert a player by ID. Deletions remove it:

```json
{"type":"player","id":"18","name":"NewPlayer"}
{"type":"player","id":"18","name":null}
```

Game creation, joins/invitation acceptance, title/capacity changes, game start and
pending-player changes send the latest complete game record:

```json
{
  "type": "game",
  "game": "3038",
  "details": {
    "game": "3038", "title": "S11-1A-P2",
    "players": ["Kayser", "Suddoman"], "invited": [],
    "player_count": 2, "started": true, "player": "Suddoman"
  }
}
```

Upsert the game by ID. Finishing or deleting a game removes it:

```json
{"type":"game","game":"3038","details":null}
```

Account renames/deletions also refresh affected unfinished games so participant
names and pending owners remain accurate. Declined invitations and administrative
participant cleanup are reflected. This adds no leave-game operation: an accepted
player cannot use the feed to leave, join or otherwise modify a game.

Messages are idempotent state updates, not unconditional notification commands.
Compare pending players against the previous cache before deciding whether to
notify. Metadata updates and duplicate events must not repeat a turn reminder.
Ordinary choices that keep the same owner, score updates, and login/preference
updates do not broadcast game or player events.

## Liveness and recovery

Send JSON `null` periodically, for example every 30 seconds. The response is
`{"type":"pong"}`. This proves liveness and renews channel-layer group membership
without database queries or engine replay. Other incoming JSON closes the
connection with code 1008. There are no feed commands for moves, chat, subscriptions
or Discord settings.

Reconnect with bounded backoff after disconnects or missing pong replies. Each
connection gets a fresh authoritative snapshot. Channel delivery is best-effort,
not a durable event log; periodically reconnect to reconcile rare dropped events
without scraping. Downtime recovers current pending turns, not historical turns.
Rapid changes can collapse to the latest state. Event handlers read current
committed data so an older queued marker cannot restore stale snapshot values.

## Server cost and persistence hooks

The initial snapshot uses three queries: registered usernames, unfinished-game
headers, and all their participant records. No replay text or rules engine is
loaded. A game update uses at most two batched metadata queries per connected
service; an account update uses one username query. Heartbeats perform no query.

Enabled Match saves add one small comparison query for turn and metadata fields.
Enabled participant saves compare only identity/invitation fields; routine
score/nation saves explicitly limit their updated fields and skip feed queries.
Account username changes refresh related games. Disabled mode adds no save query.

Normal model saves/deletions publish through Django signals after transaction
commit. Failed publication is logged and does not fail a saved game move. Bulk
`QuerySet.update()`, `bulk_create()` and raw SQL bypass save signals; code using
them to change relevant fields must call `publish_turn_change(game_id)` or
`publish_player_change(player_id)` in the same transaction. A bulk username change
also needs game updates for affected games. No subscription tables are added.

## Client migration and validation

This PR supplies the server protocol only. Switch the external notifier after
deployment: use snapshot/event caches for typeahead, game discovery, titles,
participants, `/status`, turn detection and game cleanup. Remove its recurring
webpage scans and per-match socket refreshes. Keep its own stored subscriptions,
delivery preferences, pending-turn deduplication and Discord retry queue.

Run the isolated SQLite/in-memory-channel tests with the pinned dependencies:

```sh
python -m django test Nations --settings=Nations.test_settings
```

No production secrets, Redis server, images or rules-engine checkout is required
for these tests. Production Redis/DreamHost deployment is not covered.
