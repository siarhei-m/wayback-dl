---
timeout-minutes: 10
on:
  push:
    branches: [main, master]
    paths:
      - "src/**"
      - "pyproject.toml"
  workflow_dispatch: {}
  roles: [admin, maintainer]
permissions:
  contents: read
  pull-requests: read
tools:
  github:
    toolsets: [pull_requests, issues]
safe-outputs:
  create-issue:
    title-prefix: "[docs] "
    labels: [documentation]
---
# Documentation Sync Check

Code has changed on the main branch. Check if documentation needs updating.

Steps:
1. Read the recent commits on main to understand what changed
2. Read `README.md`, `CLAUDE.md`, and `ARCHITECTURE.md`
3. Check for discrepancies:
   - New CLI flags added in `cli.py` but not documented in README
   - New modules or functions not reflected in ARCHITECTURE.md
   - Changed default values not updated in docs
   - Version number mismatches

If documentation is out of sync, create an issue listing:
- Which doc file needs updating
- What specifically is missing or wrong
- The source of truth (which code file to reference)

If everything is in sync, do nothing (no issue needed).
