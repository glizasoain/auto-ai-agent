# Guardrails Enforcement Plan

| Guardrail | Intent | Enforcement |
| --- | --- | --- |
| Issue selection (`AI_ALLOWED` + Ready) | Keep automation scoped to approved work | Runner verifies labels before launch and blocks when missing |
| Branch naming (`ai/{issueKey}-{slug}`) | Ensure traceable automation branches | Template command rejects noncompliant names |
| Forbidden dirs (`infra/**`, `secrets/**`) | Protect sensitive infrastructure | File watchers mark those paths read-only for the agent |
| Add/modify tests when code paths change | Maintain quality for automated edits | Pre-commit hook and reviewer checklist require changed/added tests |
| Tests must pass | Prevent regressions before PR | CI pipeline fails PR creation until `pytest` exits 0 |
| PR summary links Jira | Preserve audit trail | PR bot lints description and rejects missing Jira links |

These align with `guardrails.yaml` and are restated in `.cursor/rules/ai-allowed.mdc` for quick reference.
