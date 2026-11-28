---
name: oracle-oci-mcp
description: >-
  Setup, verification, and safe operational guidelines for Oracle's official OCI Cloud MCP Server
  (oracle.oci-cloud-mcp-server) in Antigravity IDE, Antigravity CLI, Claude Desktop, and Cursor.
  Use when setting up Oracle Cloud MCP access or inspecting/managing OCI cloud resources for this project.
---

# Oracle Cloud Infrastructure (OCI) MCP Skill

This skill configures and governs `oracle.oci-cloud-mcp-server` (from [oracle/mcp](https://github.com/oracle/mcp)) for managing OCI resources from your local Antigravity IDE, Antigravity CLI, Claude Desktop, or Cursor.

---

## Quick Setup (Local Machine)

### Windows (1-Click / PowerShell)
Double-click `setup_oracle_mcp.bat` in the repo root, or run in PowerShell:
```powershell
.\setup_oracle_mcp.bat
```

### macOS / Linux / Cross-Platform
```bash
python3 .agents/skills/oracle-oci-mcp/scripts/setup_oracle_mcp.py
```

What the setup script automates:
1. Installs `uv` (via `winget` on Windows if missing) and Python `3.13`.
2. Creates `C:\tmp` on Windows (required by `oracle.oci-cloud-mcp-server` v2.3.x for `/tmp/audit.log`).
3. Pre-caches `oracle.oci-cloud-mcp-server@latest` and verifies its MCP `initialize` + `tools/list` stdio handshake.
4. Safely merges `"oracle-oci-cloud"` into Antigravity's `mcp_config.json` (`~/.gemini/config/mcp_config.json` and, if present, `~/.gemini/antigravity/mcp_config.json`) with automatic `.bak` backups.
5. Validates `~/.oci/config` (and interactively helps create it if missing), then runs a read-only OCI SDK region check when credentials are present.

### Useful Flags
```bash
# Verify setup & live MCP handshake without modifying any files
python3 .agents/skills/oracle-oci-mcp/scripts/setup_oracle_mcp.py --verify-only

# Also configure Claude Desktop and Cursor global configs
python3 .agents/skills/oracle-oci-mcp/scripts/setup_oracle_mcp.py --client all

# Non-interactive run (skips ~/.oci/config creation prompt)
python3 .agents/skills/oracle-oci-mcp/scripts/setup_oracle_mcp.py --non-interactive
```

---

## Project OCI Context

- **Production host deployed by GitHub Actions:** `157.151.163.109` (see `.github/workflows/deploy.yml`, user `ubuntu`, systemd unit `hassan-ai.service` at `/home/ubuntu/hassanalifarrukh`).
- **Boundary:** `oracle.oci-cloud-mcp-server` manages **OCI control-plane resources** (Compute instances, VCNs, Security Lists/NSGs, Block Volumes, Compartments, Object Storage, IAM). OS-level inspection inside Ubuntu (`systemctl`, app logs, `.env`) still uses SSH.

---

## Low-Token Tool Workflow (`oracle-oci-cloud`)

1. If you already know the OCI Python SDK client and method, call `describe_oci_operation` or `invoke_oci_api` directly (for example `oci.core.ComputeClient` + `list_instances`, or `oci.identity.IdentityClient` + `list_compartments`).
2. If you know the service family, call `list_client_operations` with a `query` filter and small `limit`.
3. Use `find_oci_api` only as a fallback with short SDK terms like `"list instances"` or `"security list"`.
4. Keep `result_mode="auto"` (default) and pass `fields` / `max_results` when listing resources.

---

## Safety & Security Guardrails

- **Read before writing:** Always inspect current state (compartment, instance, VCN/NSG/security list) before proposing any mutation.
- **Explicit approval for mutations:** Before creating, stopping, terminating, resizing, or modifying security rules/volumes/instances, present the target OCIDs, parameters, and potential blast radius (shared NSGs/security lists, costs) and wait for explicit user confirmation.
- **Protect secrets:** Never read, print, or commit `~/.oci/config`, `*.pem` private keys, or API tokens into the repository or chat logs.
- **Verify after changes:** Re-query the modified OCI resource after any approved change to confirm its `lifecycle_state`.
