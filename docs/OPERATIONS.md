# Operations and recovery

## Read-only Ubuntu checks

```sh
systemctl is-active relay-runner
systemctl --user is-active switch-agent-controller
loginctl show-user ubuntu -p Linger
sudo docker ps --format 'table {{.Names}}\t{{.Status}}'
df -h /home/ubuntu
cd /home/ubuntu/relay-system
python3 -m app.cli status
```

Use the actual PostgreSQL container name with `docker exec CONTAINER pg_isready`. A running container is not the same as a healthy database listener. Do not reset or remove volumes to resolve connection problems.

Inspect recent service logs locally using `journalctl -u relay-runner -n 80 --no-pager`. Logs and `app.cli inspect TASK_ID` may contain private project data; sanitize before sharing. Back up the private config, SQLite ledger/artifacts, project Git history and PostgreSQL through the appropriate consistent backup method before upgrades. Keep backups private and test restoration separately.

## Common symptoms

| Symptom | Check | Action |
|---|---|---|
| Command ignored | Real mention, exact syntax, sender and room allowlist | Use USAGE.md; verify identity mapping |
| Refusal plus successful transition | Switch addressing policy versus Runner identities | Correct the narrow external-user rule; test each real account |
| Waiting after one confirmation | Current revision/hash and remaining stakeholder | This is expected; the other stakeholder must confirm |
| Planner only sends prose | Actual room delivery and completion schema | Require standalone RELAY_RESULT with structured brief |
| Operator attention | Ledger/handoff state, actual CLI failure and logs | Diagnose before retry; do not manufacture completion |
| Tests fail | Exact candidate's unittest output | Let bounded review/repair flow handle supported failures |
| Stream history gap / exit 78 | Controller connection and saved event position | Reconcile actual outstanding work; no blind restart/resend |
| Wrong project files | Handoff workspace versus launch directory | Use assigned workspace; never infer it from the channel name |
| No GitHub PR | Accepted state | Expected: publishing is not implemented in this workflow |

The `resume_review` and `timeout_operator` modules are operator recovery tools, not general Discord commands. Inspect their prerequisites and use preview mode before any mutation. Do not run recovery while an agent might still be working. Do not use raw database edits as a normal workflow control.

## Exporting a delivery

The operator first verifies task state, accepted full commit, current brief and a clean repository. `git archive` of that exact commit can export project files without private runtime state. Inspect the export for credentials and ownership metadata before publishing. A source export lacks original Git history; record provenance as in this repository's workshop example.

## Shutdown

Stopping the laptop does not stop AWS billing. Stop paid instances only when the team agrees availability can end; retained storage and other resources can still incur charges. Preserve database/project volumes and backups. Do not run volume-removal commands as cleanup during the live demo.
