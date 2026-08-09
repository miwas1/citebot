# Security policy

## Reporting a vulnerability

Please report vulnerabilities privately through the repository's GitHub **Security** tab using **Report a vulnerability**. Include a clear description, affected versions or commit, reproduction steps, impact, and any suggested mitigation. Do not disclose credentials or publish a proof of concept until a fix is available.

If private GitHub reporting is unavailable, contact the repository maintainers through the private contact listed in the repository profile. Public issue reports are not appropriate for suspected vulnerabilities.

## Scope and deployment guidance

CiteBot is a reference implementation. Set `APP_ENV=production`, configure both `RESEARCH_API_KEY` and `ADMIN_API_KEY`, keep web search and Python execution disabled unless required, and place the API behind TLS and an authenticated network boundary. The bundled Python subprocess limits are not a hostile multi-tenant sandbox.
