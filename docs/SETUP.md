# Setup and deployment

## 1. Local inspection (no credentials)

Install Python 3.11+ and Git. Clone to a short path. In PowerShell on Windows or a Linux terminal at the repository root:

```sh
git --version
python --version
python -m unittest discover -s tests -v
python -m tools.validate_pact_config --config config.example.json
```

Ubuntu may require `python3` in place of `python`. The example intentionally validates **zero enabled workflows** until real identities are configured. This is not a live readiness check. The example application has a separate test command from its own directory, documented in the main README.

## 2. Prepare the host and Switch

The tested deployment used Ubuntu, Switch Core 0.31.0, Switch Console 0.40.1, Codex CLI 0.166.0 and Git. OpenCode 1.19.5 was installed but is not the qualified model path for this demo. These are recorded pilot versions, not a claim that newer combinations are compatible.

Provisioning AWS is a human-paid action. Use an authorized account and check cost, region and storage before launching. Follow the [official self-host guide](https://docs.switchagents.ai/switch-rooms/deploy/self-host) for Switch, Gateway and PostgreSQL. This repository does not include a replacement Docker stack: preserve a working deployment and its database volumes. Current upstream setup may differ from the pinned pilot.

Before adding Runner, verify Docker services, PostgreSQL readiness, the Switch API and the remote agent controller. In Switch Console, the server selection and **agent run location** are separate settings. Select the Ubuntu host for every hosted agent. A laptop-hosted agent will still stop when that laptop sleeps.

The controller must operate persistently under the host user. The pilot used the `switch-agent-controller` user service and lingering enabled for `ubuntu`. Authenticate the model CLI privately on that host using the provider's supported method; verify a real file-reading task through Switch. Do not copy private laptop credentials into this repository.

## 3. Prepare a fresh Runner checkout (Ubuntu only)

These commands are for a **new** installation. If `/home/ubuntu/relay-system` or a Runner service already exists, inspect and back it up instead of cloning over it. This deployment path preserves the service unit's existing naming.

```sh
git clone https://github.com/Pranay22077/pactroom.git /home/ubuntu/relay-system
cd /home/ubuntu/relay-system
python3 -m unittest discover -s tests -v
install -d -m 700 /home/ubuntu/relay-state /home/ubuntu/relay-projects
cp config.example.json /home/ubuntu/relay-state/config.json
chmod 600 /home/ubuntu/relay-state/config.json
```

The copy command is for a new configuration only. Never overwrite an existing private config with the example. Inspect `policies/AGENTS.md` and install its PactRoom-scoped policy at the project root on a new host. On a host with legacy workflows, merge the scope carefully rather than replacing their instructions.

## 4. Dedicated Runner identity

Runner is a non-model Switch identity, separate from the five AI profiles. It needs access to the two rooms, not agent-management privileges. Use a server-authorized registration method and keep its key at `/home/ubuntu/relay-state/runner-token` with mode 600.

For the **inspected Core 0.31.0 deployment only**, `tools/register_runner.py` is an optional registration helper. It reads the existing registration token privately from `~/relay-switch/.env`, contacts the loopback API, and refuses to overwrite a stored identity/key. Read it before use. Only run it once if no Runner exists:

```sh
python3 -m tools.register_runner
```

Do not rerun registration after an uncertain network response: inspect server identities first. Never register a duplicate Runner just to repair a connection. Never paste the key in Discord, issues or screenshots.

## 5. Rooms and agent profiles

Create two private Discord channels and link them to distinct Switch rooms through the installed connector. Invite Runner to both rooms. Add these profiles with the corresponding files from `agent-instructions/`:

| Profile | Room | Launch directory on Ubuntu |
|---|---|---|
| pact-facilitator | Team | `/home/ubuntu/pactroom-agent-homes/facilitator` |
| pact-planner | Engine | `/home/ubuntu/pactroom-agent-homes/planner` |
| pact-builder | Engine | `/home/ubuntu/pactroom-agent-homes/builder` |
| pact-reviewer | Engine | `/home/ubuntu/pactroom-agent-homes/reviewer` |
| pact-steward | Engine | `/home/ubuntu/pactroom-agent-homes/steward` |

Create the folders first using `mkdir -p` on Ubuntu. These are launch homes; actual work uses the workspace from each Runner handoff. Keep the reviewer in its assigned exact-commit copy. Use Codex provider on the remote host, safe automatic actions only, no permission bypass, and no agent-management permission. Restrict specialist addressing to Runner plus the operator for diagnostics. Restrict facilitator addressing to the intended team; do not trigger on arbitrary conversation.

Switch's addressing policy must allow the three intended humans to address Runner. The policy resolver's external-user IDs and the authenticated event sender IDs consumed by Runner are different fields in the qualified installation. Inspect actual identity mappings; do not guess IDs from display names or use the wrong field. Verify a status request from each real account after configuration.

## 6. Configure identities

Edit the private config, never the public example. Replace:

- `server_id`: stable name for this deployment; do not change it after creating workspaces.
- `switch_url`: Core API address, not the Gateway HTML dashboard. Loopback HTTP is allowed; remote connections require verified HTTPS.
- `runner_agent_id`, `connection_id`, `token_file`: dedicated identity, unique stable connection and private key path.
- `rooms`: actual team and engine room IDs with display names.
- `workflows.pactroom`: same room IDs, three distinct human event sender IDs and four specialist event sender IDs/names.

Global `humans` and `roles` are empty in this PactRoom-only example. They are reserved for legacy Relay workflows; do not duplicate Pact identities there. `workspace_owner_room_id` must equal the team room. Keep `python-unittest`, two repairs and both required confirmers.

Set `workflows.pactroom.enabled` to `true` after replacing all placeholders. Keep top-level `live_enabled` false while validating:

```sh
cd /home/ubuntu/relay-system
python3 -m tools.validate_pact_config --config /home/ubuntu/relay-state/config.json
python3 -m app.cli probe-switch --config /home/ubuntu/relay-state/config.json
```

Expected: one enabled workflow validates, and the authenticated probe lists both intended rooms. The probe can initialize local ledger directories; it does not dispatch an agent. Do not publish its private room metadata.

## 7. Start one worker and qualify live

Set `live_enabled` true only after identities, membership and model tools are verified. First test in the foreground:

```sh
python3 -m app.cli serve --config /home/ubuntu/relay-state/config.json
```

Use the commands in [USAGE.md](USAGE.md) with the actual three people. Confirm that one confirmation waits, the second starts work, real tests and review complete, stale evidence is refused and acceptance does not publish. Do not run a second worker against the same state. Stop the foreground worker with Ctrl+C before installing the service.

For a new authorized installation, inspect `deploy/relay-runner.service` and then:

```sh
sudo install -m 644 deploy/relay-runner.service /etc/systemd/system/relay-runner.service
sudo systemctl daemon-reload
sudo systemctl enable --now relay-runner
systemctl is-active relay-runner
```

The unit assumes user `ubuntu` and the paths above. Adapt all paths consistently if using another account. It does not install Switch or the agent controller. Validate continuous operation with the laptop disconnected; cloud services and provider authentication must remain available.
