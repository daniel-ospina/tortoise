## Summary

<!-- What this PR does and why. Reference the tracked issue. -->

Closes #<issue-number>

## Surface change (MCP tools / public SDK methods)

<!-- Only relevant if this PR touches tortoise/tool_registry.py, tortoise/mcp_server.py's
     @mcp.tool()/add_tool/add_transform calls, or the public methods on TortoiseSDK in
     tortoise/sdk.py. See CONTRIBUTING.md §"The MCP tool surface and public SDK methods
     cannot grow without Daniel's approval". -->

- [ ] This PR **does not** add, remove or rename an MCP tool or a public SDK method
- [ ] **OR** it does, and **Daniel approved it BEFORE the change** (raised as a USER QUESTION /
      DECISION RELAY per `AGENTS.md`), with the approval recorded on the row(s) in
      `config/surface-manifest.yml`. *(A green `surface-guard` is not approval — the gate catches
      an unrecorded drift only; a PR that updates the code and re-cuts the baseline passes it.)*

## Contribution license

By submitting this PR, I donate this contribution to the Tortoise project under
the **BSD 3-Clause License** (or public domain — see CONTRIBUTING.md):

- [ ] I donate this contribution under the BSD 3-Clause License (this project is
      BSL 1.1 → MPL 2.0; see CONTRIBUTING.md → Contribution license note)

## Checklist

- [ ] Change is linked to a tracked issue
- [ ] Tests pass locally (`uv run pytest tests/ -v` where applicable)
- [ ] CI is green
