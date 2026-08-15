# Deploy CiteBot on a private server

This runbook deploys CiteBot for one person or a small trusted organization on
a private Linux server. It keeps inference and document processing on that
server, exposes only the web application through HTTPS, and retains the API,
Qdrant, embedding model, and language model on private interfaces.

## 1. Choose the host

The default Compose profile targets a CPU-only machine with at least:

- 4 modern CPU cores; 8 cores are preferable during OCR and generation;
- 16 GB RAM with at least 2 GB kept available under sustained load;
- 40 GB free disk before documents, indexes, and backups;
- 64-bit Linux, Docker Engine, and Docker Compose v2; and
- a private DNS name such as `citebot.example.com` if browser access is remote.

Use encrypted disks when documents are confidential. Keep the server patched,
enable automatic security updates, and restrict SSH to keys and trusted source
networks. CiteBot is designed for a single trusted deployment; projects provide
document and query isolation, but the application does not yet provide
per-user accounts or project-level permissions.

## 2. Install and configure

Clone a tagged release or a reviewed commit:

```bash
git clone https://github.com/miwas1/citebot.git
cd citebot
git checkout <release-tag-or-reviewed-commit>
cp .env.example .env
```

Generate two different secrets. Do not reuse an SSH, database, or proxy
password:

```bash
openssl rand -hex 32
openssl rand -hex 32
```

Set at least these values in `.env`:

```dotenv
APP_ENV=production
RUNTIME_MODE=offline
API_BIND_HOST=127.0.0.1
CITEBOT_PORT=8000
RESEARCH_API_KEY=<first-generated-secret>
ADMIN_API_KEY=<second-generated-secret>
MODEL_ARTIFACT_ROOT=./models
```

Use restrictive permissions because `.env` contains credentials:

```bash
chmod 600 .env
mkdir -p storage models
chmod 700 storage models
```

`RESEARCH_API_KEY` allows chat and conversation access. `ADMIN_API_KEY` allows
document uploads, ingestion status, search administration, and evaluation
administration. Give the admin key only to people who manage the library.

## 3. Provision models

Model provisioning is the only normal step that requires internet access. It
downloads the configured artifacts, pins their upstream revisions, and records
checksums in `models/manifest.lock.json`:

```bash
make local-setup
```

After provisioning, the offline runtime rejects hosted inference and web search
and uses the local manifest. Review readiness and container state:

```bash
docker compose ps
docker compose logs --tail=100 api document-worker
```

The first offline startup automatically creates the **Sample Project** and
queues the bundled corpus at `/app/data/sample_corpus`. Open the workspace
while `document-worker` processes it; the project becomes **Ready to query**
when indexing completes. The bootstrap is idempotent. Set
`SAMPLE_CORPUS_AUTO_INGEST=false` only when intentionally starting without the
bundled sample data.

The API is reachable internally at `http://127.0.0.1:8000`. Caddy publishes
CiteBot on port 80 and Dozzle at `/dozzle/` on the same listener, with port 8888
as an optional dedicated listener. Qdrant, embedding, and LLM ports remain
exposed only on the internal Compose network.

## 4. Optional: put HTTPS in front

The Compose stack serves plain HTTP for simple EC2/private-network use. For a
public deployment, put HTTPS in front of the Caddy listener and do not expose
Dozzle without additional access control. The built-in Caddy service can be
changed to use a real hostname instead of `:80` if you want Caddy to manage
certificates directly.

### Caddy example

Install Caddy on the host and create `/etc/caddy/Caddyfile`:

```caddyfile
citebot.example.com {
    encode zstd gzip
    reverse_proxy 127.0.0.1:8000
    request_body {
        max_size 26MB
    }
    header {
        Strict-Transport-Security "max-age=31536000; includeSubDomains"
        X-Content-Type-Options "nosniff"
        Referrer-Policy "same-origin"
        -Server
    }
}
```

Point the DNS record at the server, allow inbound TCP 80 and 443, and reload
Caddy. It will obtain and renew a public certificate when the hostname is
publicly resolvable. For a private-only DNS name, configure your organization's
internal certificate issuer instead.

### nginx example

If nginx already terminates TLS, add this server after configuring a valid
certificate:

```nginx
server {
    listen 443 ssl http2;
    server_name citebot.example.com;

    ssl_certificate /etc/letsencrypt/live/citebot.example.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/citebot.example.com/privkey.pem;
    client_max_body_size 26m;

    add_header Strict-Transport-Security "max-age=31536000; includeSubDomains" always;
    add_header X-Content-Type-Options "nosniff" always;
    add_header Referrer-Policy "same-origin" always;

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto https;
        proxy_read_timeout 180s;
        proxy_buffering off;
    }
}
```

Do not change the Compose API binding to `0.0.0.0`. Caddy should remain the
public listener. Restrict 8000, 6333, 8081, and 8082 at the host and cloud
firewalls. Restrict Dozzle's 8888 listener as well unless it is intentionally
public. Prefer VPN or identity-aware proxy access for business use.

## 5. Verify the deployment

From the server:

```bash
curl --fail http://127.0.0.1:8000/api/v1/health
curl --fail http://127.0.0.1:8000/api/v1/ready
```

From an authorized client, open `https://citebot.example.com/`. In the gear
menu, enter the research and admin API keys. Then:

1. open the **Guide** link from the dashboard if you need the operator reference;
2. confirm the bundled sample documents or upload a small text or PDF document;
3. wait until its status changes to **Ready**;
4. ask a question whose answer is present in the document;
5. open a citation and confirm its supporting passage; and
6. refresh the browser and confirm the conversation remains in **Recent**.

A `401` response normally means the corresponding browser key is missing or
incorrect. A document that remains queued usually means `document-worker` is
not healthy. Check `docker compose ps` and the worker logs before retrying.

## 6. Back up and restore

`storage/` contains source uploads, extracted documents, SQLite metadata and
conversation state, ingestion jobs, and the sparse index. Back it up as
sensitive data. Qdrant vectors are derived from those documents and can be
rebuilt, but keeping a Docker volume snapshot shortens recovery.

For a simple consistent cold backup:

```bash
docker compose stop
tar --xattrs --acls -czf /secure-backups/citebot-storage-YYYY-MM-DD.tar.gz storage
cp models/manifest.lock.json /secure-backups/manifest.lock-YYYY-MM-DD.json
docker compose up -d
```

Replace the date in the filename before running the command. Encrypt backups,
store at least one copy on a different system, restrict access, and test a
restore periodically. Model binaries can be re-provisioned from the locked
manifest; back them up too when upstream availability is an operational risk.

To restore, stop Compose, move the damaged `storage/` aside, extract the chosen
archive into the repository root, verify ownership and permissions, restore the
matching model manifest/artifacts, and start Compose. Confirm `/ready`, upload
history, a known conversation, and a cited query before returning service.

## 7. Upgrade safely

Before an upgrade:

1. read the release notes and `CHANGELOG.md`;
2. take and verify a cold backup;
3. record the deployed Git revision and model-manifest checksum; and
4. keep the prior image/revision available for rollback.

Then update and rebuild:

```bash
git fetch --tags
git checkout <new-reviewed-release>
docker compose build --pull api document-worker
docker compose up -d
docker compose ps
docker compose logs --tail=100 api document-worker
```

Run the verification flow from section 5. If it fails, stop the new stack,
return to the recorded revision, restore the pre-upgrade backup when data or
schema state changed, and start the prior stack.

## 8. Operational boundaries

- CiteBot API keys are shared secrets, not named user accounts or roles.
- Anyone with the admin key can manage ingestion and access administrative APIs.
- Browser keys live in local storage; use managed, encrypted devices and clear
  site data when a device is reassigned.
- Do not enable web search for private documents unless the data-handling impact
  is explicitly accepted. Offline mode keeps it disabled.
- The optional Python tool is not a hostile multi-tenant sandbox. Leave it
  disabled for shared deployments.
- Monitor disk capacity, available RAM, worker failures, and backup completion.
- Rotate both API keys after suspected exposure and whenever access membership
  changes; restart the API after editing `.env`.
