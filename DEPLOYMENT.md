# I Need A Smile — Deployment Profile

This document captures how the Smile app is deployed on the GoDaddy host so a new
developer can find the runtime files, configuration includes, and operational
controls without hunting.

> **Authoritative runtime location (server)**
>
> - App root: `/opt/apps/I_Need_A_Smile`
> - This is outside cPanel’s `/home/<user>` tree, so it will not appear in the
>   cPanel File Manager.

---

## 1) Application layout (server)

```
/opt/apps/I_Need_A_Smile
├── app.py
├── inspiration_tags.py
├── requirements.txt
├── templates/
├── static/
│   ├── generated/
│   └── album_images/
└── prompt_log.txt   (created at runtime)
```

Key runtime paths are derived in code:

- `APP_ROOT`: directory of `app.py`
- `static/generated/`: generated AI images
- `static/album_images/`: saved album images
- `prompt_log.txt`: prompt history

Reference: `app.py` constants `APP_ROOT`, `GENERATED_DIR`, `ALBUM_DIR`,
`PROMPT_LOG_PATH`.

---

## 2) How the app is started

### Production (current host)

The app is launched via **Gunicorn** on the host. Confirm the exact startup
command with:

```bash
ps aux | grep gunicorn
```

This should show a command similar to:

```
/opt/apps/I_Need_A_Smile/venv/bin/gunicorn --bind 127.0.0.1:8000 app:app
```

If Gunicorn is launched from a shell session (parent PID is a session scope),
then it is **manual** and not guaranteed to survive reboot. If a systemd unit is
used, it will show up in `systemctl status <PID>`.

### Development

For local development only:

```bash
python app.py
```

This runs Flask’s built-in dev server on port 5000.

---

## 3) Environment variables (required + optional)

**Required for image generation**

- `SMILE_IMAGE_API_KEY` **or** `OPENAI_API_KEY`

**Optional**

- `SMILE_SECRET` (Flask session secret)
- `SMILE_IMAGE_API_URL` (override API endpoint)
- `SMILE_IMAGE_MODEL` (default `gpt-image-1`)
- `SMILE_IMAGE_SIZE` (default `1024x1024`)

These are read directly in `app.py` when generating images.

---

## 4) Host-level configuration / includes

Because the app is proxied via the host web server, it may rely on host-level
includes that live **outside** the app repo. Capture those here for onboarding.

> **TODO: fill in the actual include locations on the host**
>
> Add the exact file paths once confirmed on the server, for example:
> - Apache/Nginx vhost include files
> - cPanel include snippets
> - Proxy or socket configuration pointing to the Gunicorn bind
>
> Suggested places to check (server):
> - `/etc/httpd/` (Apache)
> - `/etc/apache2/` (Apache on Debian/Ubuntu)
> - `/etc/nginx/` (Nginx)

---

## 5) Operations quick notes

**Generate endpoint**

- `POST /generate_async` triggers image generation.

**Storage**

- Generated images are written to `static/generated/`.
- Saved album images are written to `static/album_images/`.

**Logs**

- Prompt history: `/opt/apps/I_Need_A_Smile/prompt_log.txt`
- Gunicorn logs depend on launch flags (e.g., `--error-logfile`).

---

## 6) Verification commands (server)

Check that Gunicorn is running:

```bash
ps aux | grep gunicorn
```

Check that the app responds locally (if bound to 127.0.0.1:8000):

```bash
curl -i -X POST http://127.0.0.1:8000/generate_async
```

