# Using PactRoom in Discord

Create a private team channel and a separate engine channel. Each maps to its own Switch room. Team: humans, facilitator and Runner. Engine: Runner, planner, builder, reviewer and steward. Match room and sender IDs in configuration before enabling.

Use the actual Discord mention selector for `@relay-runner`. Send each command as a new message, by itself. Do not append questions to exact status, brief, evidence, confirmation or acceptance commands. Replace placeholders with Runner's displayed values.

| Who | Command | Result |
|---|---|---|
| Delivery lead | `@relay-runner /pact start Build a small Python workshop registration library with two places, unique nonempty IDs, FIFO waitlist and unittest tests. Cancellation is a later change. Do not publish.` | Planner prepares version 1 |
| Any of the three configured humans | `@relay-runner /pact status TASK_ID` | Current state |
| Any configured human | `@relay-runner /pact brief TASK_ID` | Current requirements |
| Client and Operations, separately | `@relay-runner /pact confirm TASK_ID v1 BRIEF_HASH` | Record each person's agreement; both are required |
| Any configured human | `@relay-runner /pact evidence TASK_ID` | Compact verification summary |
| Delivery lead | `@relay-runner /pact accept TASK_ID v1 BRIEF_HASH COMMIT_SHA` | Accept reviewed delivery |
| Delivery lead | `@relay-runner /pact change TASK_ID Add cancellation: more than 120 minutes before start, promote FIFO automatically; at 120 minutes or closer, reserve the vacancy for explicit staff promotion. Reject mutations at or after start. Use UTC-aware time and deterministic tests.` | Plan a new revision; require fresh confirmations |

Use the exact generated confirmation and acceptance commands, including the current revision. Never reuse version-1 hashes for version 2. Client and Operations must be different people; the delivery lead is a third person. Configured identity, not a matching display name or quoted approval, establishes authority.

For general questions, mention `@pact-facilitator` and ask normally. Runner is a command processor, not a general chatbot. Engine agents respond to authenticated Runner assignments; people do not manually invoke each stage.

There is no `/pact publish` command. The current workflow ends at acceptance. Export and GitHub publication are separate operator actions. Files are on the worker host; Discord does not itself store a working directory. After inspection, an operator can copy the project or publish an authorized clean repository for teammates to clone.

## Example team discussion

Client: “When someone cancels, give the next waiting person the seat automatically.”

Operations: “Close to the start we must approve replacements manually.”

Facilitator: summarize the disagreement and ask for the cutoff, without deciding it for them.

Lead: start or change the task using the agreed rule. Both stakeholders read the brief and independently confirm it. Runner then executes the delivery pipeline.

## What should you see?

Planning → waiting for both confirmations → building → reviewing → preparing delivery → ready for acceptance → accepted. A review defect may return to building. A requirement change invalidates old readiness. A real blocker results in operator attention, not a false success.
