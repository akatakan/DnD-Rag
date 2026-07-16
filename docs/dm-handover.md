# DM Control and Handover

The game owner is permanent. Active DM control can move without changing ownership.

- The owner can assign one player as co-DM and choose the disconnect fallback.
- A co-DM receives the DM view, but game controls remain read-only until a handover is accepted.
- When the active DM disconnects, a 60-second grace period starts. Reconnecting cancels it.
- After the grace period, an online co-DM receives the control offer.
- Without an online co-DM, the game enters assisted mode or starts a player vote for AI DM, according to the configured fallback.
- An AI takeover requires at least half of eligible players to approve it.
- The owner can reclaim human DM control at any time.
- The active DM can switch between human, assisted, and AI modes while the game is running.

Set `DM_GRACE_SECONDS` to change the grace period for local development or tests.
