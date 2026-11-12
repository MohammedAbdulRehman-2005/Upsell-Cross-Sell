# Render Public Deployment Guide — Plural WhatsApp Commerce Automation

This guide explains how to deploy the **Plural WhatsApp Upsell & Cross-Sell Service** to **Render** using your existing PostgreSQL database (`plural-upsell-db`).

---

## Architecture Overview

```
                      ┌──────────────────────────────────────────────┐
                      │                 Render.com                   │
                      │                                              │
                      │   ┌──────────────────────────────────────┐   │
                      │   │ Web Service: plural-upsell-service   │   │
                      │   │ (Docker / Python 3.12 / FastAPI)     │   │
                      │   │                                      │   │
  Public Traffic ────┼──>│ • Customer Storefront (/)            │   │
                      │   │ • WhatsApp Inbox (/simulator)        │   │
                      │   │ • Admin Dashboard                    │   │
                      │   │ • Demo Delivery Worker (12s trigger) │   │
                      │   │ • Ready Probe (/health/ready)        │   │
                      │   └──────────────────┬───────────────────┘   │
                      │                      │                       │
                      │        Internal URL  │ (Fast, Private Net)   │
                      │                      ▼                       │
                      │   ┌──────────────────────────────────────┐   │
                      │   │ Managed PostgreSQL: plural-upsell-db │   │
                      │   │ Service ID: dpg-dbqsanrbc2fs73e9ko40-a│  │
                      │   │ (70 Products, 420 Mappings, State)   │   │
                      │   └──────────────────────────────────────┘   │
                      └──────────────────────────────────────────────┘
```

---

## Step 1: Copy your Database Connection String

1. In the **Render Dashboard**, open your existing database: **`plural-upsell-db`** (`dpg-dbqsanrbc2fs73e9ko40-a`).
2. Click the **Connect** button at the top-right corner of the database page.
3. Under the **Internal Connection** tab, copy the **Internal Database URL**:
   ```
   postgres://plural_user:PASSWORD@dpg-dbqsanrbc2fs73e9ko40-a/plural_upsell_db
   ```
   *(Note: The internal URL is recommended for Web Services hosted on Render in the same region because it communicates over Render's private network).*

---

## Step 2: Create the Web Service on Render

### Option A: Using the Render Dashboard (Recommended)

1. On [dashboard.render.com](https://dashboard.render.com), click **+ New** (top right) ➔ **Web Service**.
2. Select **Build and deploy from a Git repository** and connect:
   ```
   https://github.com/MohammedAbdulRehman-2005/Upsell-Cross-Sell
   ```
3. Configure the service settings:
   - **Name**: `plural-upsell-service` (or any preferred name)
   - **Region**: `Oregon (US West)` *(matches your `plural-upsell-db` region)*
   - **Branch**: `main`
   - **Runtime**: **Docker**
   - **Dockerfile Path**: `Dockerfile` (or `backend/Dockerfile`)
   - **Docker Context**: `.`
   - **Instance Type**: `Free`

4. Scroll down to **Environment Variables** and add the following keys:

| Key | Value | Description |
|---|---|---|
| `DATABASE_URL` | *`[Paste your Internal Database URL]`* | The application automatically normalizes `postgres://` to `postgresql+psycopg://` |
| `ENVIRONMENT` | `production` | Production mode |
| `WHATSAPP_PROVIDER` | `simulator` | Uses internal simulator (or `meta_cloud_api` if you have Meta tokens) |
| `DEMO_DELIVERY_ENABLED` | `true` | Enables background worker for automated 12s demo delivery |
| `DEMO_DELIVERY_DELAY_SECONDS` | `12` | Delivery threshold in seconds |
| `LOG_LEVEL` | `INFO` | Application log level |
| `CORS_ORIGINS` | `*` | Permits storefront API access |

5. Under **Advanced Settings**:
   - **Health Check Path**: `/health/ready`
   - **Auto-Deploy**: `Yes`

6. Click **Create Web Service**.

---

### Option B: Using Render Blueprint (`render.yaml`)

If using Render Blueprints:
1. Go to **Blueprints** on Render Dashboard.
2. Select your repository `Upsell-Cross-Sell`.
3. Render reads `render.yaml` automatically.
4. When prompted for `DATABASE_URL`, paste the Internal Database URL of `plural-upsell-db`.
5. Click **Apply**.

---

## Step 3: What Happens During Deployment

The container uses our custom entrypoint (`backend/entrypoint.sh`), which executes automatically:

1. **Database Migration**:
   Runs `alembic upgrade head` to verify and build all relational tables (`products`, `product_mappings`, `customers`, `orders`, `campaigns`, `message_logs`, `demo_delivery_events`).
2. **Idempotent Data Seeding**:
   Runs `python -m app.scripts.seed_catalogue --apply-synthetic-fixture`. This seeds all **70 catalogue products** and **420 ranked recommendation mappings** from the authoritative workbook and synthetic metadata.
3. **Port Binding**:
   Render dynamically passes `$PORT` (typically `10000`). The entrypoint binds Uvicorn to `0.0.0.0:${PORT}`.
4. **Health Probe**:
   Render's health check queries `GET /health/ready`. Once PostgreSQL connection and catalogue count verification pass, Render marks the service **Live**.

---

## Step 4: Verification Checklist

Once Render marks the service as **Live**, verify the public URL (e.g. `https://plural-upsell-service.onrender.com`):

| Page / Endpoint | URL | Expected Behavior |
|---|---|---|
| **Health Check** | `/health/ready` | Returns `{"status":"UP","checks":{"database":"UP"}}` |
| **Storefront** | `/` or `/store` | Full customer-facing storefront with products, cart, and checkout modal |
| **WhatsApp Inbox** | `/simulator?view=whatsapp-inbox` | Three-pane admin inbox with collapsible customer drawer and real-time messages |
| **Campaign Simulator** | `/simulator` | Interactive campaign preview and recommendation tester |
| **Order Tracking** | `/store/order/{order_id}` | Real-time progress bar showing demo progression (Placed ➔ Packed ➔ Out for Delivery ➔ Delivered in ~12s) |
