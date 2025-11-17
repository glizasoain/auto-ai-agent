<!-- Guardrails governance plan -->
# Guardrails Plan for Autonomous Aider Agent

## Purpose
Define how guardrails are authored, reviewed, and enforced for the fully autonomous Aider workflow. This plan applies only to the automated Prefect/Kubernetes runs; manual developers can reference it but may choose lighter-weight processes.

## Source of Truth
- `guardrails.yaml` (repo root) is the canonical specification.
- All guardrail changes must be made via pull requests that modify this YAML file.
- `.cursor/rules/ai-allowed.mdc` remains for backward compatibility but will be phased out in favor of the YAML spec.

## YAML Schema Summary
```yaml
version: <semver>
last_updated: <ISO date>
contact: <owner email>
scope:
  description: ...
constraints:
  - id: ...
    description: ...
    enforcement: prompt|read-only|post-command
work_items:
  jira_issue:
    summary_prompt: ...
forbidden_paths:
  - glob
required_steps:
  - id: ...
    description: ...
post_run:
  default_command: ""
future_extensions:
  - notes...
```

## Update Workflow
1. **Propose**: Open a PR editing `guardrails.yaml`. Include rationale and any downstream changes needed (e.g., secrets, runner behavior).
2. **Review**: At least one member of the autonomous-agent team must approve. Consider:
   - Does the change conflict with current automation capabilities?
   - Are new forbidden paths/read-only rules enforceable today?
   - Do post-run commands require additional dependencies?
3. **Version bump**: Update `version` and `last_updated` fields.
4. **Merge & deploy**:
   - Once merged, rebuild the runner image if enforcement logic changed.
   - Recreate the Kubernetes secret if new env vars or commands are required.

## Enforcement Pipeline (Current Design)
1. **Repo clone**: The runner clones the target repo, ensuring `guardrails.yaml` is present.
2. **Parsing** (to be implemented):
   - Entry point reads `guardrails.yaml`.
   - Constraints are rendered into the Aider prompt.
   - `forbidden_paths` eventually map to read-only enforcement (next phase).
   - `post_run.default_command` seeds the `POST_RUN_COMMAND` env var when not overridden.
3. **Runtime**:
   - Prefect injects issue metadata (`ISSUE_KEY`, `ISSUE_SUMMARY`) for Jira runs.
   - Aider executes with the composed prompt.
   - Optional post-run commands enforce “tests must pass.”

## Runner Loading & Enforcement (Detailed Design)
1. **Load YAML**
   ```bash
   GUARDRAILS_FILE=${GUARDRAILS_FILE:-"./guardrails.yaml"}
   RULES_JSON=$(python - <<'PY'
   import yaml, json, sys
   path = sys.argv[1]
   data = yaml.safe_load(open(path))
   print(json.dumps(data))
   PY  "$GUARDRAILS_FILE")
   ```
2. **Prompt Construction**
   - Extract `constraints`, `required_steps`, and appropriate `work_items.*.summary_prompt`.
   - Compose a single string injected into `RUN_PROMPT` before launching Aider.
   - Example (pseudo):
     ```bash
     GUARDRAIL_TEXT=$(python - <<'PY'
     import json, sys
     data = json.loads(sys.stdin.read())
     constraints = "\n".join(f"- {c['description']}" for c in data["constraints"])
     steps = "\n".join(f"{i+1}) {s['description']}" for i, s in enumerate(data["required_steps"]))
     print(f"Guardrails:\\n{constraints}\\nSteps:\\n{steps}")
     PY <<<"$RULES_JSON")
     RUN_PROMPT="$GUARDRAIL_TEXT\n\n${RUN_PROMPT}"
     ```
3. **Forbidden Paths (Future)**
   - Map `forbidden_paths` to read-only mounts or `.aider.conf.yml` `read_only_paths`.
   - For now, include a reminder in the prompt; next iteration can create a temporary config file for Aider.
4. **Post-Run Command**
   - If `POST_RUN_COMMAND` env var is empty, default to `post_run.default_command` from YAML.
   - Allows guardrails to mandate `npm test` or similar per repo without editing secrets manually.

This design keeps automation self-contained (no external services) and makes it easy to extend enforcement logic over time.

## Future Enhancements
- Tie `forbidden_paths` into actual read-only mounts or `.aider.conf.yml`.
- Allow rule overrides per repository or per issue type.
- Emit telemetry/logging when guardrails are triggered for easier auditing.
- Integrate with Jira/Priority metadata (e.g., severity-based constraints).

## Appendix
- Current guardrail spec: see `guardrails.yaml`.
- Runner integration notes: `runner/entrypoint.sh` (prompt injection, `POST_RUN_COMMAND` hook).

