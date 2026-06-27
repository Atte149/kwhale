# Security Policy

## Reporting a Vulnerability

If you discover a security vulnerability in KWhale, **please do not open a
public GitHub issue.**

Instead, report it privately:

1. Go to [GitHub Security Advisories](https://github.com/Atte149/kwhale/security/advisories/new)
2. Create a new private security advisory
3. Include a description of the vulnerability, steps to reproduce, and potential impact

You can also email the maintainer directly if you prefer.

## Response Timeline

- **Acknowledgment:** Within 48 hours
- **Initial assessment:** Within 7 days
- **Fix or mitigation:** Depends on severity, typically within 30 days for
  high-severity issues

## Security Best Practices for Deployments

- **Never commit `.env` files** — they contain secrets. The `.gitignore` is
  pre-configured to exclude them.
- **Use strong passwords** for PostgreSQL and Navidrome admin accounts.
- **Run behind a reverse proxy** (Caddy, nginx, Traefik) with TLS.
- **Restrict access** to the API and Navidrome ports (bind to `127.0.0.1` and
  proxy through your reverse proxy).
- **Keep your API keys secure** — store them in `.env` files with restrictive
  permissions (`chmod 600`).
- **Update regularly** — pull the latest changes and rebuild images.

## What to Report

- Authentication bypasses
- SQL injection
- Path traversal
- Remote code execution
- Exposure of secrets or user data
- SSRF vulnerabilities in source plugins