# Browser Reporter Server

This repository contains a FastAPI backend and a PostgreSQL database for receiving browser activity reports from the Browser Reporter extension and presenting them in a simple web dashboard.

## Running with Docker

1.  Clone the repository on a machine inside your LAN.

2.  Set environment variables (optional)

```
# .env (not committed)
API_KEY=your-secure-api-key-here
SESSION_SECRET=some-long-random-string
```
These values are also defined with defaults in `docker-compose.yml` – the file above will pick them from the host environment if present.

3.  Build & start

```bash
docker compose up --build -d
```

The first time you start the stack it will:
* download the `postgres:15` image,
* build the `backend` image (Python 3.12 + FastAPI + Uvicorn),
* create the database schema,
* create a default admin dashboard account `admin` / `admin`.

4.  Access the services

* Backend API:   `http://<host>:8000/api/reports/data`
* Dashboard UI:  `http://<host>:8000`  (login with the admin credentials above)
* PostgreSQL:    `localhost:5432` (`browser_reporter` / `browser_reporter`)

5.  Updating

If you make code changes, rebuild the container:

```bash
docker compose up --build -d backend
```

6.  Stopping & removing

```bash
docker compose down
```

The volume `db_data` keeps your database between restarts. Delete it if you need a fresh start:

```bash
docker volume rm <project_name>_db_data
```

---

### Health checks & scaling
* The backend is stateless; scale it with `docker compose up --scale backend=3 -d` and put Nginx/Traefik in front if desired.
* PostgreSQL writes are modest (~1 k row/s). One instance handles the load easily.

### Production hardening (even inside a LAN)
* Change the default passwords immediately.
* Restrict port 5432 exposure if not needed outside the compose network.
* Enable nightly database backups.

---

## System Documentation

* [GPT Expansion Documentation](GPT_EXPANSION_DOCUMENTATION.md) - Details about disk expansion performed on June 15, 2024
