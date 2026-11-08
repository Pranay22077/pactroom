# PactRoom

<p>
  <a href="https://discord.gg/QSd8BU5Dt"><img src="https://img.shields.io/badge/Discord-Join_the_community-344A51?style=for-the-badge&logo=discord&logoColor=white" alt="Join PactRoom on Discord"></a>
  <a href="https://dev.to/pranay22077/two-people-one-brief-building-pactroom-with-switch-discord-and-aws-2a42"><img src="https://img.shields.io/badge/DEV-Read_the_story-25343C?style=for-the-badge&logo=devdotto&logoColor=white" alt="Read the PactRoom build walkthrough on DEV"></a>
  <a href="docs/WORKFLOW.md"><img src="https://img.shields.io/badge/Workflow-Explore_the_system-45676A?style=for-the-badge&logo=mermaid&logoColor=white" alt="Explore the complete PactRoom workflow"></a>
</p>

**Turn team decisions into reviewed software deliveries.**

PactRoom connects people and coding agents through Discord and Switch. It records agreement on a versioned brief, coordinates implementation and independent review, and lets the delivery lead accept the exact result.

Two stakeholders must confirm the same requirements before coding begins. If those requirements change, the previous confirmations no longer authorize work on the new version.

[Full workflow](docs/WORKFLOW.md) · [Quick start](#quick-start) · [Deployment guide](docs/SETUP.md) · [Discord commands](docs/USAGE.md) · [Build walkthrough](https://dev.to/pranay22077/two-people-one-brief-building-pactroom-with-switch-discord-and-aws-2a42)

## Why PactRoom?

A client wants canceled workshop seats filled automatically. Operations wants staff to approve replacements close to the start. Both requests are reasonable, but an agent cannot infer which policy the team agreed to implement.

PactRoom makes that agreement explicit. People settle the policy; agents implement it; the coordinator checks the evidence before presenting a delivery for acceptance.

- **Versioned agreement:** confirmations identify the brief revision and content hash.
- **Two-person confirmation:** Client and Operations are distinct configured people; one confirmation keeps the task waiting.
- **Coordinated execution:** Python Runner dispatches each specialist and validates its response before the next handoff.
- **Independent review:** Reviewer inspects a separate copy of the candidate commit.
- **Bounded repair:** findings return to Builder for at most two repair cycles before operator attention is required.
- **Traceable delivery:** test results, findings and acceptance refer to the same requirements and code candidate.

## How it works

```mermaid
%%{init: {"theme":"base","themeVariables":{"primaryColor":"#EDF3F2","primaryTextColor":"#24383D","primaryBorderColor":"#63817F","lineColor":"#7B8D91","secondaryColor":"#F5F7F8","tertiaryColor":"#E3EBED","fontFamily":"Arial, sans-serif","fontSize":"15px"},"flowchart":{"curve":"basis","nodeSpacing":28,"rankSpacing":38}}}%%
flowchart TD
    Discuss[Team discusses requirements with Facilitator] --> Plan[Planner prepares versioned brief]
    Plan --> Gate{Both stakeholders confirm current brief?}
    Gate -->|Not yet| Wait[Wait for missing confirmation]
    Wait --> Gate
    Gate -->|Yes| Build[Builder implements and commits]
    Build --> Tests[Runner executes tests]
    Tests -->|Pass| Review[Reviewer checks exact candidate]
    Tests -->|Fail| Repair{Repair budget available?}
    Review -->|Changes requested| Repair
    Repair -->|Yes: at most two cycles| Build
    Repair -->|No| Attention[Operator attention]
    Review -->|Approved| Steward[Steward prepares delivery]
    Steward --> Accept[Lead accepts exact reviewed result]

    classDef human fill:#EDF3F2,stroke:#63817F,color:#24383D,stroke-width:1.5px;
    classDef process fill:#F7F9FA,stroke:#A1B1B4,color:#263A40;
    classDef control fill:#344F54,stroke:#344F54,color:#FFFFFF,stroke-width:1.5px;
    classDef hold fill:#E6EBED,stroke:#778C93,color:#263A40,stroke-dasharray:4 3;
    class Discuss,Accept human;
    class Plan,Build,Tests,Review,Steward process;
    class Gate,Repair control;
    class Wait,Attention hold;
```

All specialist handoffs are controlled by Runner through Switch. The arrows describe the execution sequence, not agents independently calling one another. A changed brief starts a new agreement cycle and makes earlier delivery evidence stale.

**Acceptance records a human decision. It does not push code, create a pull request, merge changes or deploy an application.**

## Two rooms, clear responsibilities

The **team room** contains the three people, Facilitator and Runner. It holds discussions, confirmations, progress summaries and acceptance decisions. The **engine room** contains Runner and four specialists, keeping technical assignments and detailed results separate from team conversation.

| Participant | Responsibility |
|---|---|
| Delivery lead | Starts and revises tasks; accepts the reviewed result |
| Client | Confirms the required product behavior |
| Operations | Confirms operational constraints |
| Facilitator | Explains disagreements and decision ownership; cannot confirm for people |
| Planner | Produces requirement IDs, owners and acceptance examples |
| Builder | Implements the brief and supplies a candidate commit |
| Reviewer | Checks the specified brief and candidate independently |
| Steward | Prepares the delivery summary and supporting evidence |
| Python Runner | Validates identities, persists state, schedules work, runs tests and checks results |

[Explore the full workflow](docs/WORKFLOW.md) for setup, identity checks, confirmation gates, retries, changes and acceptance.

The five agent profiles use Codex in the demonstrated deployment. Runner is ordinary Python code, not another model. Role instructions are in [agent-instructions/](agent-instructions/).

## Delivery evidence in Discord

The team receives a short summary rather than raw agent output. This captured version-2 response reports 18 passing application tests, an approved independent review and acceptance of the candidate.

![Actual Discord evidence showing 18 passing tests, independent review, requirement mapping and human acceptance.](docs/assets/screenshots/delivery-evidence.jpg)

*Recorded pilot response, 7 November 2026. Nine of ten requirements had builder-supplied test references; this is not a claim of complete test coverage. Detailed records remain on the execution host.*

## Quick start

Inspect the coordinator and run the example without Switch credentials, model access or cloud resources. Local checks require **Python 3.11+ and Git** and use the Python standard library.

Clone into a short path, such as `C:\src\pactroom` on Windows or `~/pactroom` on Linux:

```sh
git clone https://github.com/Pranay22077/pactroom.git
cd pactroom

# Coordinator tests: simulated transport and temporary Git repositories.
python -m unittest discover -s tests -v

# Validate the disabled example configuration.
python -m tools.validate_pact_config --config config.example.json

# Run the generated application's separate test suite.
cd examples/workshop-registration
python -m unittest discover -s tests -v
```

On Ubuntu, use `python3` if `python` is unavailable. The example configuration intentionally has **zero enabled workflows** until real identities are supplied. Validation is not a live connection test.

Windows supports local checks and Switch Console. The persistent Runner worker requires Linux because it uses a POSIX process lock. See [verification notes](docs/VERIFICATION.md) for recorded results and platform-specific skips.

## Architecture and hosting

[Switch](https://github.com/sandbox-quantum/switch) supplies rooms, agent identities, message transport and Discord integration. Runner supplies the agreement state machine and delivery checks.

```mermaid
%%{init: {"theme":"base","themeVariables":{"primaryColor":"#EDF3F2","primaryTextColor":"#24383D","primaryBorderColor":"#63817F","lineColor":"#7B8D91","secondaryColor":"#F5F7F8","tertiaryColor":"#E3EBED","fontFamily":"Arial, sans-serif","fontSize":"15px"},"flowchart":{"curve":"basis","nodeSpacing":28,"rankSpacing":38}}}%%
flowchart TB
    Discord[People in Discord] <-->|Discord connector| Core
    Console[Switch Console / browser] <-->|Authorized private access| Gateway
    subgraph AWS[Ubuntu host on AWS EC2]
        subgraph Docker[Docker services]
            Gateway[Switch Gateway] <--> Core[Switch Core]
            Core <--> Postgres[(PostgreSQL)]
        end
        Core <--> Runner[Python Runner]
        Runner <--> Ledger[(SQLite workflow ledger)]
        Core <--> Controller[Persistent agent controller]
        Controller --> CLI[Codex CLI sessions]
        CLI --> Workspace[Room-owned Git workspace]
        Workspace --> Review[Separate exact-commit review copy]
        Runner --> Evidence[Test and delivery records]
    end
    CLI <-->|Inference requests| Provider[External model provider]

    classDef client fill:#EDF3F2,stroke:#63817F,color:#24383D;
    classDef service fill:#F7F9FA,stroke:#A1B1B4,color:#263A40;
    classDef control fill:#344F54,stroke:#344F54,color:#FFFFFF;
    classDef storage fill:#E3EBED,stroke:#778C93,color:#263A40;
    class Discord,Console,Provider client;
    class Gateway,Core,Controller,CLI service;
    class Runner control;
    class Postgres,Ledger,Workspace,Review,Evidence storage;
    style AWS fill:#FAFBFC,stroke:#A1B1B4,color:#24383D;
    style Docker fill:#EDF3F2,stroke:#63817F,color:#24383D;
```

The laptop is a client; source files, tests, agent execution and workflow state live on the Ubuntu host. EC2 does not host model weights. Execution can continue while laptops sleep as long as cloud services, network and provider access remain available.

Specialists work on one room-owned project. Agent home directories are launch locations, and Reviewer uses a separate candidate copy. Runner serializes autonomous work; people must coordinate their own edits. Shared directories and role prompts are not operating-system isolation.

<details>
<summary>See the Builder configured on the remote host</summary>

![Switch Console showing pact-builder using Codex on Relay AWS, role instructions and its remote launch directory.](docs/assets/screenshots/builder-on-aws.jpg)

*Recorded configuration, not a recommended permission template. Apply the addressing restrictions in the setup guide; do not copy the screenshot's broad “Anyone” selection.*

</details>

### Connect a live team

1. Prepare a Linux host and a working Switch deployment with its Discord connector.
2. Create separate team and engine rooms and configure the five Codex profiles.
3. Register a dedicated Runner identity and configure three distinct human identities.
4. Store credentials outside the checkout and validate a private configuration.
5. Verify room membership, provider access and a real agent interaction before enabling the worker.
6. Run one Runner service and test confirmation, review and acceptance with the actual teammates.

Follow the [deployment guide](docs/SETUP.md) for commands, recorded component versions and recovery checks. The existing hosted dashboard is private and requires authorized network access and an individual Switch account; it is not a public demo endpoint.

## Using it from Discord

Mention `@pact-facilitator` for discussion. Mention `@relay-runner` for workflow commands, using Discord's actual mention selector. Send each command as its own message.

| Action | Command |
|---|---|
| Start a task — lead | `@relay-runner /pact start YOUR_REQUIREMENTS` |
| Inspect progress | `@relay-runner /pact status TASK_ID` |
| Read the current brief | `@relay-runner /pact brief TASK_ID` |
| Confirm — Client and Operations separately | `@relay-runner /pact confirm TASK_ID v1 BRIEF_HASH` |
| Inspect delivery evidence | `@relay-runner /pact evidence TASK_ID` |
| Accept — lead | `@relay-runner /pact accept TASK_ID v1 BRIEF_HASH COMMIT_SHA` |
| Change requirements — lead | `@relay-runner /pact change TASK_ID UPDATED_REQUIREMENTS` |

Copy confirmation and acceptance commands from Runner's current response: `v1`, IDs and hashes above are examples, not reusable values. Runner checks configured identities, not display names or quoted approval text. See [usage and conversations](docs/USAGE.md).

## Included example: workshop registration

[examples/workshop-registration/](examples/workshop-registration/) contains the accepted output of the two-version pilot, with 18 application tests. It demonstrates how a negotiated policy becomes executable behavior:

| Situation | Behavior |
|---|---|
| Places available | Register participants into confirmed places |
| Capacity reached | Add participants to a FIFO waitlist |
| Cancellation more than 120 minutes before start | Automatically promote the next waiting participant |
| Cancellation at exactly 120 minutes or closer, before start | Reserve the vacancy for explicit staff promotion |
| New arrival while a replacement is pending | Cannot bypass the existing queue |
| Mutation at or after event start | Reject the operation |
| Invalid operation | Preserve existing state |

The library uses UTC-aware time and deterministic tests. It is an in-memory example, not a hosted booking product, and does not implement staff authentication. The coordinator is the reusable project; the library is one generated delivery. [Example provenance and checks](docs/VERIFICATION.md) distinguish historical live results from local tests.

## Repository guide

| Path | Contents |
|---|---|
| [app/](app/) | Coordinator, Switch adapter, workflow state and notifications |
| [tests/](tests/) | Offline workflow, identity, evidence, recovery and formatting tests |
| [agent-instructions/](agent-instructions/) | Facilitator and specialist role instructions |
| [config.example.json](config.example.json) | Disabled template without live credentials |
| [deploy/](deploy/) | Example Linux service configuration |
| [examples/workshop-registration/](examples/workshop-registration/) | Generated library and its tests |
| [docs/](docs/) | Setup, operation, architecture and verification documentation |

## Operating boundaries

- Changed requirements need fresh confirmations. Changed candidates need matching verification before acceptance.
- Hashes detect changed inputs; they do not establish correctness or make evidence tamper-proof.
- Cloud hosting does not remove provider usage limits or guarantee uninterrupted availability.
- GitHub delivery is a separate operator action. There is no `/pact publish` command.
- Keep tokens, SSH keys, private configuration, ledgers and private conversations out of version control.

Read [security and limitations](SECURITY.md) before allowing agents to execute on a shared host, and [operations and recovery](docs/OPERATIONS.md) before changing a running installation.

## Contributing

Open an issue with expected behavior, observed behavior and reproduction steps. For code changes, include a focused regression test and run the coordinator suite. Run the example suite when changing the workshop library. Remove private identifiers and credentials from logs before sharing them.

## Further reading

- [Build walkthrough: Two people, one brief](https://dev.to/pranay22077/two-people-one-brief-building-pactroom-with-switch-discord-and-aws-2a42)
- [Architecture and authoritative records](docs/ARCHITECTURE.md)
- [Setup and deployment](docs/SETUP.md)
- [Build history and attribution](docs/BUILD_HISTORY.md)

## License

[MIT](LICENSE).
