# Security Considerations

INTERCEPT is designed as a **local signal intelligence tool** for personal use on trusted networks. This document outlines security considerations and best practices.

## Network Binding

By default, INTERCEPT binds to `0.0.0.0:5050`, making it accessible from any network interface. This is convenient for accessing the web UI from other devices on your local network, but has security implications:

### Recommendations

1. **Firewall Rules**: If you don't need remote access, configure your firewall to block external access to port 5050:
   ```bash
   # Linux (iptables)
   sudo iptables -A INPUT -p tcp --dport 5050 -s 127.0.0.1 -j ACCEPT
   sudo iptables -A INPUT -p tcp --dport 5050 -j DROP

   # macOS (pf)
   echo "block in on en0 proto tcp from any to any port 5050" | sudo pfctl -ef -
   ```

2. **Bind to Localhost**: For local-only access, set the host or use the CLI flag:
   ```bash
   sudo ./start.sh -H 127.0.0.1
   ```

3. **Trusted Networks Only**: Only run INTERCEPT on networks you trust. There is no longer a shipped default password — see [Authentication](#authentication) for how the first-run credentials work.

## Authentication

INTERCEPT uses username/password authentication. **There is no default password.**

### First run

On first start, if `INTERCEPT_ADMIN_PASSWORD` is not set, INTERCEPT generates a random password for the `admin` account and:

- logs it at startup, and
- writes it to `instance/.initial_password`

Log in with that password. You will be required to set your own before the interface becomes usable — every other page and API route is blocked until you do. Delete `instance/.initial_password` once you have changed it.

If you set `INTERCEPT_ADMIN_PASSWORD` yourself, that is treated as your own choice and no change is forced.

### Changing your password

`/change-password`, reachable at any time from the interface. It requires your current password and a minimum of 12 characters.

### Why this changed

Until v2.33.6, `ADMIN_PASSWORD` defaulted to `admin`. Any installation that never set the environment variable therefore shipped with **publicly known credentials**, and the documentation told you so.

That mattered more than it might appear, because of what else was reachable. Until the same release, the `/controller/*` API required no authentication at all: the global login gate skipped it on the assumption that those routes authenticated callers themselves, which they did not. Anyone able to reach the port could list remote agents, read their API keys, register or delete agents, and start or stop SDR hardware on remote nodes.

The two together meant that an INTERCEPT instance reachable on a network could be taken over with credentials printed in its own README. Both are fixed:

| Release | Change |
| --- | --- |
| 2.33.6 | `/controller/*` authenticates every route; agent API keys removed from API responses; WebSocket endpoints verify the session; the `admin` default removed |
| 2.33.8 | A password you did not choose must be changed before the interface is usable |

**If you are upgrading** and your instance still uses `admin`/`admin`, you will be prompted to change it at your next login. This is deliberate and cannot be skipped.

**If you run remote agents**, note that 2.33.6 also made an API key mandatory for agent push. An agent registered without one will be refused by `/controller/api/ingest`. Set a key on each agent, and the matching `controller_api_key` in the agent's own configuration.

### Further protection

For additional protection when exposing INTERCEPT beyond your local machine:

1. Use a reverse proxy (nginx, Caddy) with authentication or TLS
2. Use a VPN to access your home network
3. Use SSH port forwarding: `ssh -L 5050:localhost:5050 your-server`

## Security Headers

INTERCEPT includes the following security headers on all responses:

| Header | Value | Purpose |
|--------|-------|---------|
| `X-Content-Type-Options` | `nosniff` | Prevent MIME type sniffing |
| `X-Frame-Options` | `SAMEORIGIN` | Prevent clickjacking |
| `X-XSS-Protection` | `1; mode=block` | Enable browser XSS filter |
| `Referrer-Policy` | `strict-origin-when-cross-origin` | Control referrer information |
| `Permissions-Policy` | `geolocation=(self), microphone=()` | Restrict browser features |

## Input Validation

All user inputs are validated before use:

- **Network interface names**: Validated against strict regex pattern
- **Bluetooth interface names**: Must match `hciX` format
- **MAC addresses**: Validated format
- **Frequencies**: Validated range and format
- **File paths**: Protected against directory traversal
- **HTML output**: All user-provided content is escaped

## Subprocess Execution

INTERCEPT executes external tools (rtl_fm, airodump-ng, etc.) via subprocess. Security measures:

- **No shell execution**: All subprocess calls use list arguments, not shell strings
- **Input validation**: All user-provided arguments are validated before use
- **Process isolation**: Each tool runs in its own process with limited permissions

## Debug Mode

Debug mode is **disabled by default**. If enabled via `INTERCEPT_DEBUG=true`:

- The Werkzeug debugger PIN is disabled (not needed for local tool)
- Additional logging is enabled
- Stack traces are shown on errors

**Never run in debug mode on untrusted networks.**

## Reporting Security Issues

If you discover a security vulnerability, please report it by:

1. Opening a GitHub issue (for non-sensitive issues)
2. Emailing the maintainer directly (for sensitive issues)

Please include:
- Description of the vulnerability
- Steps to reproduce
- Potential impact
- Suggested fix (if any)
