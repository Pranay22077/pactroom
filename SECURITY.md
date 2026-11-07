# Security and operating boundaries

Do not commit `.env`, private configuration, Runner keys, model credentials, SSH keys, databases, full room transcripts or private handoff archives. This repository uses placeholders. Give each teammate an individual Switch account and separately authorized network access. Never distribute the host owner's credentials.

The qualified pilot uses private Tailscale access. A private tailnet URL is not a public judge-accessible demo. Provide a video and local test instructions; arrange authorized access separately if required. Do not expose the database or raw model/agent endpoints to the internet for convenience.

Switch permissions and Runner validation are separate layers. In the tested deployment, Switch addressing rules use resolved external-user IDs; Runner command authorization uses authenticated event sender IDs. Mixing them caused contradictory refusal messages. Verify both layers using the intended user's actual account.

Agents share an Ubuntu account in this pilot. Separate prompts, home folders, Git branches and review copies are not hardened tenant isolation. Python tests execute code on the worker. Use only trusted project inputs; do not offer this configuration as a public arbitrary-code service. A fixed test command prevents arbitrary command strings, not malicious Python code.

The live workflow never publishes automatically. Stop and investigate uncertain or timed-out work rather than replaying it. Never reset a database or delete volumes as a routine recovery action.

Report a suspected vulnerability privately to the repository owner before including sensitive details in a public issue. Do not send tokens in an issue.
