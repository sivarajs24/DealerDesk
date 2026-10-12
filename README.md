# VIKMO DealerDesk — AI / ML Track

Track: `aiml`  
Video Walkthrough: [Video Link](https://youtu.be/placeholder)

---

## Overview

DealerDesk is an ordering, inventory, demand forecasting, and conversational assistant backend designed for automotive and motorcycle parts distribution.

### Built Components
- **Core Backend (§4, §5, §6, §7.1)**: Relational schema in PostgreSQL with Product catalogue, Inventory, Dealer profiles, and Draft Order lifecycle.
- **Order Engine (§7.2–§7.5)**: Status transitions, concurrency-safe stock deduction using row-level locking (`select_for_update`), cancellation stock restoration, exact 18% GST rounded `ROUND_HALF_UP` to the paisa, and duplicate-safe idempotency.
- **Dealer Assistant (§9)**: Conversational assistant using Google Gemini with live PostgreSQL grounding, tool calling (`search_parts`, `check_stock`, `create_draft_order`), multi-turn session persistence, and untrusted catalogue text guardrails.
- **Forecasting & Reorder (§10)**: 4-week demand forecast across 40 SKUs using LightGBM Tweedie count regression, beating historical baselines by 20.68%. Safety stock and reorder points served live via `GET /api/reorder-suggestions/`.

---

## Technical Specifications

- **Backend**: Django 4.2+, Django REST Framework
- **Database**: PostgreSQL
- **LLM Provider**: Google Gemini (`gemini-1.5-flash` / `gemini-2.0-flash` via `google.genai`)
- **Key Variable**: `GEMINI_API_KEY`

---

## Setup & Run Contract

```bash
# 1. Install dependencies
pip install -r requirements.txt

# 2. Database migrations
python manage.py migrate --noinput

# 3. Seed initial data (idempotent, safe to run multiple times)
python manage.py seed_data
python manage.py seed_data

# 4. Run application
python manage.py runserver 0.0.0.0:8000 --noreload
```

### Environment Variables (.env)
```env
DATABASE_URL=postgresql://user:password@host:5432/dealerdesk
GEMINI_API_KEY=your_gemini_api_key
SUPPLIER_API_URL=http://127.0.0.1:8099
SUPPLIER_API_KEY=dev-supplier-key
DJANGO_SECRET_KEY=secret-key
```

---

## Verification & Testing

### Smoke Test
```bash
python smoke_test.py --base-url http://localhost:8000 --track aiml
```
*(All 10/10 checks passing)*

### Test Suite
```bash
python manage.py test
```

### ML Forecasting
The forecasting pipeline and analysis are documented in `ml/forecasting.ipynb`. Final predictions are committed in `ml/forecast.csv` (160 rows).

---

## Forecasting Validation Results

Historical backtest window (`2026-08-24` to `2026-09-14`):

| Model | MAE | RMSE | Error Reduction |
|---|---|---|---|
| Last Value | 12.394 | 22.319 | — |
| Same Week Last Year (YoY) | 11.425 | 18.187 | — |
| Recent 4W Average | 8.597 | 13.863 | Baseline |
| **LightGBM Multi-Feature Model** | **6.819** | **10.415** | **-20.68% MAE** |
