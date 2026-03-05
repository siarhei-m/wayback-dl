---
timeout-minutes: 10
on:
  schedule:
    cron: "0 9 * * 1"
  workflow_dispatch: {}
  roles: [admin, maintainer]
permissions:
  contents: read
safe-outputs:
  create-issue:
    title-prefix: "[tests] "
    labels: [enhancement, good-first-issue]
---
# Test Coverage Review

Analyze test coverage in ${{ github.repository }} and suggest improvements.

Steps:
1. Read all files in `src/wayback_dl/` to understand the codebase
2. Read all tests in `tests/test_downloader.py`
3. Identify functions, methods, or code paths that are NOT tested
4. Focus on:
   - Edge cases in existing functions
   - Error handling paths
   - Security-related code (path traversal, URL validation, symlink checks)
   - The download() method's async behavior

Create an issue listing the top 5 most valuable untested code paths, with:
- The function/method name and file
- What scenario is not covered
- A brief description of what the test should verify

Skip trivial getters/setters. Focus on logic that could break.
