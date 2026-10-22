# Security and deployment boundaries

agenthub is a **LAN / single-user** tool that assumes trusted users on the network. It does not provide multi-tenant isolation, a login system, or fine-grained permissions. Add your own security layer before any public deployment.

## Risk model

- The hub at `8670` has no login layer, and the Settings write API is unauthenticated: anyone who can reach it can add or remove hosts, change the display name, and trigger proxy restarts.
- `8671–8679` proxy each node's opencode Web UI; the hub injects the node's Basic Auth server-side, so **access to the hub is equivalent to access to those nodes**.
- Embedded opencode can read and write project files and execute commands/tools. Exposing opencode through agenthub exposes those capabilities to any browser that can reach the hub.
- MCP (`8680`) is read-only but is still a data egress path; it has only a bearer token by default and no OAuth authorization page.
- Config may contain node addresses, usernames, and `password_env` names; real passwords live in `~/.config/agenthub/env`.

## Minimum hardening

1. **Network isolation**: listen only on the LAN or `127.0.0.1`, and use a firewall to restrict sources for `8670–8680`; never port-forward them to the internet.
2. **Reverse proxy + authentication**: for remote access, put Caddy/Nginx in front with HTTPS and Basic Auth, OAuth2 Proxy, Authelia, or a VPN; authentication must cover `8670` and every `8671+` port.
3. **TLS**: do not run a credential-carrying proxy over plaintext HTTP on a public network; provide HTTPS for the hub and node ports to avoid mixed-content iframe failures.
4. **Least privilege**: run each opencode node as a dedicated low-privilege system user, restrict accessible directories, and keep unrelated credentials off the nodes.
5. **Protect local files**: set `~/.config/agenthub/env` and `config.yaml` to `600`; never commit them.
6. **MCP**: keep it bound to `127.0.0.1`, use a long random `AGENTHUB_MCP_TOKEN`, configure `allowed_hosts` / `allowed_origins`, and put OAuth 2.1/authorization in front before exposing it through a tunnel.
7. **Audit and updates**: keep systemd/journal logs; re-verify the proxy, iframe, and API mappings after opencode upgrades; review dependency changes before upgrading.
8. **Publishing**: scan git history for secrets before making a repository public; grant the GitHub mirror token the minimum scope for the target repository and rotate it regularly.

## Explicitly not provided

- User accounts, session login, RBAC, or tenant isolation.
- Internet-grade rate limiting/WAF, compliance auditing, or secret management.
- Secondary approval or sandboxing for opencode tool calls.

Implement these according to your own environment.

## Disclaimer

This software is provided "as is" without any express or implied warranty. Deployers must evaluate network boundaries, access control, and data sensitivity themselves; any loss caused by misconfiguration, public exposure, or excessive permissions is the deployer's responsibility.
