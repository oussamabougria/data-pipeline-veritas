# Veritas Monitoring Dashboard

READ-ONLY live view of the industrial data pipeline.

## Run (3 commands)

```bash
pip install -r requirements.txt
```

```bash
set VERITAS_DB_CONN=postgresql://veritas:veritas_app_2024@localhost:5434/veritas
uvicorn api:app --host 0.0.0.0 --port 8888 --reload
```

Then open `dashboard.html` in Chrome.

## Endpoints

| Endpoint | Description |
|---|---|
| `GET /api/confidence` | Last 50 scored observations |
| `GET /api/stats` | Aggregate pipeline stats |
| `GET /api/audit` | Last 30 audit log entries |
| `GET /api/sensors` | Latest observation per sensor |
| `GET /health` | API liveness check |
