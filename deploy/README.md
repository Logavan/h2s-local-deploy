# Reverse proxy + TLS termination

The Flask backend (`:8080`) and Next.js frontend (`:3000`) both speak plain HTTP.
For any production / client-VM deployment, terminate TLS in front of both.

This directory ships an nginx config that:

- Listens on `:443` for HTTPS, redirects `:80` → `:443`
- Proxies `/api/*` and `/health` → `backend:8080`
- Proxies everything else → `frontend:3000`
- Adds HSTS, X-Frame-Options, X-Content-Type-Options
- Sets `client_max_body_size 200m` for bulk ZIP uploads

## Option A — System nginx on the host VM (simplest)

```bash
# 1. Install nginx (Ubuntu/Debian)
sudo apt-get update && sudo apt-get install -y nginx

# 2. Copy this config
sudo cp deploy/nginx.conf /etc/nginx/conf.d/hanacv2sql.conf

# 3. Drop your TLS cert + key into place
sudo mkdir -p /etc/nginx/certs
sudo cp /path/to/hanacv2sql.crt /etc/nginx/certs/
sudo cp /path/to/hanacv2sql.key /etc/nginx/certs/

# 4. Edit the config - replace 'hanacv2sql.client.local' with your hostname

# 5. Reload
sudo nginx -t && sudo systemctl reload nginx
```

## Option B — Let's Encrypt (free, auto-renewing)

```bash
sudo apt-get install -y certbot python3-certbot-nginx
sudo certbot --nginx -d hanacv2sql.client.local
# Certbot will edit the config in place to add the issued cert paths.
```

Auto-renewal is handled by a certbot timer that ships with the package.
The `:80` server block already allows `/.well-known/acme-challenge/` so
http-01 challenges work without changes.

## Option C — nginx as a sidecar container

Add this service to `docker-compose.enterprise.yml`:

```yaml
  nginx:
    image: nginx:1.27-alpine
    ports:
      - "80:80"
      - "443:443"
    volumes:
      - ./deploy/nginx.conf:/etc/nginx/nginx.conf:ro
      - ./deploy/certs:/etc/nginx/certs:ro
    depends_on:
      - backend
      - frontend
    networks:
      - hanacv2sql-network
    restart: unless-stopped
```

Then place certs at `deploy/certs/hanacv2sql.crt` and `hanacv2sql.key`.

## Firewall

If the VM has a host firewall, only these ports need to be open inbound:

- `:443/tcp` — user traffic
- `:80/tcp` — ACME challenges + HTTP→HTTPS redirect (optional)
- `:22/tcp` — admin SSH only

Backend `:8080` and frontend `:3000` should **not** be exposed publicly —
the nginx sidecar (or system nginx) is the only thing that talks to them.

## CORS reminder

`H2S_ALLOWED_ORIGINS` in `backend/.env` must include the public hostname
this proxy exposes (e.g. `https://hanacv2sql.client.local`). The Flask app
refuses to start in production without it.