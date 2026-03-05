---
timeout-minutes: 5
on:
  issues:
    types: [opened, reopened]
  roles: [admin, maintainer, write]
permissions:
  issues: read
tools:
  github:
    toolsets: [issues, labels]
safe-outputs:
  add-labels:
    allowed: [bug, feature, enhancement, documentation, question, good-first-issue]
  add-comment: {}
---
# Issue Triage

Analyze the new issue #${{ github.event.issue.number }} in ${{ github.repository }}.

Read the issue title and body carefully. This is a Python CLI tool (wayback-dl) that
downloads websites from the Internet Archive Wayback Machine using the CDX API.

Based on the content, add exactly ONE label:
- `bug` — something is broken or not working as expected
- `feature` — request for new functionality
- `enhancement` — improvement to existing functionality
- `documentation` — documentation needs updating
- `question` — user asking for help or clarification
- `good-first-issue` — simple enough for a first-time contributor

After labeling, post a brief comment:
1. Thank the author for the report
2. Explain why you chose that label
3. If it's a bug, mention relevant source files that might be involved
4. If it's a feature, mention if it's already in PLANNED_WORK.md
