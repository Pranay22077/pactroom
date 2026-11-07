# Build history and attribution

Relay's generic room-workspace coordinator and the local/AWS setup began before the hackathon. PactRoom adds the distinct team/engine agreement workflow: three human roles, two stakeholder confirmations, versioned requirements, exact-commit review, changed-requirement invalidation, bounded repair, acceptance and readable evidence.

This submission packages the existing implementation into a clean public repository. Its first public Git commit is a packaging commit, not evidence that every line was written that day. Event eligibility for reused foundations remains subject to the organizers' interpretation; disclose the foundation rather than implying a wholly event-created system.

The workshop example is an output generated through the deployed agent workflow. It is not the coordinator itself. Source export provenance is recorded in VERIFICATION.md. This packaging work does not rewrite or deploy the running AWS system.

## Reused components

- [Switch](https://github.com/sandbox-quantum/switch): room and agent platform, deployed separately; its source is not vendored here.
- [Switch documentation](https://docs.switchagents.ai/): setup, providers and deployment guidance.
- [Codex](https://github.com/openai/codex): coding-agent runtime used by the pilot, deployed separately.
- Python standard library: coordinator and unittest tests.
- Git: candidate commits and exact-commit archives.
- Discord, Docker, PostgreSQL, AWS EC2 and Tailscale: conversation, service hosting, storage and private access.

These products retain their own licenses and terms. No ownership of Switch or Codex is claimed. The MIT license covers the contributed repository files, not external services or their dependencies.
