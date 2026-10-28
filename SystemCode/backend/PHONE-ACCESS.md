# Temporary public phone access

## Plan and boundaries

1. Verify original direct TCP reachability separately from proxy reachability, server listeners,
   certificate validity, host firewall and current Web/GPU health.
2. Stage a checksum-pinned Cloudflare client on the existing application server. Use an explicit
   temporary HTTPS tunnel to a dedicated loopback-only guest bridge; preserve the original entry,
   credentials, backend processes and colleague GPU connection.
3. Admit one exact tunnel Host and matching Origin, retain the original public path allowlist,
   strip credentials and translate only trusted authority/CSP edges. Test forbidden paths,
   foreign origins, WebSockets and speech before publishing the new URL.
4. Verify direct access without the local proxy and request a phone confirmation. Record the
   temporary hostname, checks and teardown procedure in a structured deployment report.

## Operation and constraints

This is an explicitly selected demonstration route through Cloudflare, not an automatic fallback.
The tunnel runs on the application server; the user's laptop is not part of its runtime. Only the
existing guest page, its approved assets/APIs and five public WebSocket routes are reachable.
Provider credentials and GPU/MCP/admin listeners remain private. Browser traffic traverses
Cloudflare; the extra hop may change latency and must not be presented as a measured speed gain.

Quick Tunnels have no uptime guarantee, support at most 200 in-flight requests and change hostname
when recreated. See [Cloudflare Quick Tunnels](https://developers.cloudflare.com/tunnel/get-started/quick-tunnels/).
For a lasting service, use an account-owned stable domain/tunnel and resolve the original public
IP connectivity with the cloud/network operator. The original IP fault is not repaired by a new URL.

The dedicated systemd unit has no automatic restart and the connector disables automatic update
and connection retries. Stop only `prs-phone-access.service` to close the temporary entrance.
Remove only `/etc/nginx/conf.d/prs-phone-access.conf` after verifying ownership, then test and reload
Nginx. Do not stop the existing Web service or colleague GPU tunnel. An archived binary may remain
for reproducibility; no tunnel credentials or SSH passwords belong in repository files.

## Review and activation

Render an approved hostname without writing system configuration:

```text
python tools/render_phone_access.py generated-phone-example.trycloudflare.com
```

The pinned connector for this delivery is `cloudflared 2026.10.5`, official Linux amd64 SHA-256
`e1bc0cf084fd9e590554fe769a86b1db46701c3f847a43d902f7b974a4e16298`.
Verify against the [official release](https://github.com/cloudflare/cloudflared/releases/tag/2026.10.5)
before execution. The connector uses one HTTP/2 connection to the edge, an explicit loopback
origin `http://127.0.0.1:18770`, `--retries 0` and `--no-autoupdate`. Its systemd service uses a
private dynamic user, read-only system protection, a private temporary directory, a 192 MiB
memory cap and no automatic restart. Metrics listen only on `127.0.0.1:18772`.

Install the rendered configuration only at the named dedicated Nginx path after reviewing the
exact generated hostname. Run `nginx -t` before reloading Nginx. On validation failure, remove
only the newly owned candidate; do not alter global map-hash settings or the existing virtual host.
Test allowed pages, CSP, wrong Host/Origin, forbidden MCP/admin paths, direct TLS, WebSocket echo
and real speech. The original application and GPU services need no restart. Deployment receipts
under `benchmarks/2026-10-28/phone-public-access/` retain the concrete instance and results.
