---
timeout-minutes: 10
on:
  check_suite:
    types: [completed]
    conclusions: [failure]
  roles: [admin, maintainer]
permissions:
  contents: read
  checks: read
  pull-requests: read
tools:
  github:
    toolsets: [pull_requests, checks]
safe-outputs:
  add-comment:
    target: pull-request
---
# CI Failure Analysis

A CI check has failed. Investigate the failure and help the developer fix it.

Steps:
1. Read the failed check run logs
2. Identify the root cause (test failure, lint error, dependency issue, etc.)
3. Look at the relevant source code to understand why it failed
4. Post a comment on the associated pull request with:
   - A clear summary of what failed and why
   - The specific file and line causing the issue
   - A suggested fix (code snippet if possible)

Keep the comment concise and actionable. Don't repeat the full log output.
