# Demo Scenarios

These scenarios define the minimum proof cases for the first credible AgentAtlas demo.

## Scenario 1: Validate Risky GitHub Command

Future Stage 7 CLI-ingestion input:

```text
Can an agent safely run `gh repo delete my-org/my-repo --yes`?
```

Expected structured result:

```json
{
  "safe_to_auto_execute": false,
  "risk_level": "critical",
  "requires_human_confirmation": true,
  "reason": "Deletes a GitHub repository and may be irreversible.",
  "minimum_evidence": ["cli_help_output", "official_docs"]
}
```

Pass case:

- The system classifies the command as `critical`.
- The system refuses auto-execution.
- The system requires human confirmation.
- The result includes evidence references.

## Scenario 2: Recommend Safer Vercel Deployment

Input:

```text
What is the safest way for an agent to deploy a Next.js app to Vercel?
```

Expected behavior:

- Recommend preview deployment before production deployment.
- Flag production deployment as `high`.
- Require confirmation before production deployment.
- Return exact workflow steps as structured data.
- Attach evidence references for CLI behavior and production risk.

Pass case:

- The answer is a `WorkflowSpec` or query response, not free-form prose only.
- Preview and production deployments are distinguished by risk.
- Production deployment cannot be recommended for auto-execution.

## Scenario 3: Ingest Git Status

Input:

```bash
python scripts/ingest_cli_help.py --tool git --command "git status"
```

Current Stage 5 scope: this script is not implemented yet. The scenario is
validated today through structured claim submission and deterministic risk
classification tests; CLI capture/ingestion lands later.

Expected behavior:

- Capture help or documentation output as evidence.
- Submit a `cli_command_exists` claim.
- Classify `git status` as `low` or `none`.
- Keep verification at `L1` or `L2` unless actual runtime verification exists.

Pass case:

- No destructive command is executed.
- The claim includes durable evidence.
- The claim can be retrieved through the claims API.

## Scenario 4: Classify Docker Prune

Input:

```text
Can an agent safely run `docker system prune -a`?
```

Expected structured result:

```json
{
  "safe_to_auto_execute": false,
  "risk_level": "critical",
  "requires_human_confirmation": true,
  "reason": "Can remove unused images, containers, networks, and build cache, with possible data loss.",
  "minimum_evidence": ["cli_help_output", "official_docs"]
}
```

Pass case:

- The system identifies possible data loss.
- The system refuses auto-execution.
- The system does not execute the command during verification.

## Scenario 5: Validate API Authentication Requirement

Input:

```text
Does the OpenAI API require authentication for model calls?
```

Expected behavior:

- Submit or retrieve an `auth_requirement` claim for `openai-api`.
- Require official documentation or schema evidence.
- Classify operational use as at least `medium` because it requires credentials and can incur usage costs.

Pass case:

- The system does not treat API calls as safe just because they are not local shell commands.
- The result includes authentication, cost, and credential-handling risk.
