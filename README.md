# VIKMO DealerDesk — AI / ML Track

**Track:** `aiml`  
**Video Walkthrough:** https://drive.google.com/file/d/1MZnSHrST7ez_pa26_qS0KnaaWy8LuFjC/view?usp=sharing

---

## 1. Executive Overview

DealerDesk is an AI-native commerce and ordering operating system engineered for automotive and motorcycle spare parts distribution in South India. It unifies a relational PostgreSQL ordering engine, a conversational GenAI assistant with real-time stock grounding, and an end-to-end machine learning demand forecasting pipeline.

### Attempted Assignment Components

| Part | Description | Status |
|---|---|---|
| **A1 · Core Backend** | Relational catalogue (600 SKUs), live inventory, 25 dealers, draft order API | **Required (Completed)** |
| **C · Dealer Assistant** | Google Gemini conversational assistant with live DB grounding & tool calling | **Required (Completed)** |
| **D · Forecasting & Reorder** | 4-week demand forecast (40 SKUs), baseline benchmarks, reorder suggestions | **Required (Completed)** |
| **A2 · Order Engine** | Pessimistic locking (`select_for_update`), exact 18% GST, idempotency, lifecycle | **Attempted (Completed Bonus)** |

---

## 2. Technical Stack & Specifications

- **Backend Framework:** Django 4.2+, Django REST Framework (DRF)
- **Database:** PostgreSQL (connected via `DATABASE_URL` with native connection pooling)
- **LLM Provider & Model:** Google Gemini (`gemini-2.0-flash` / `gemini-1.5-flash`) via official modern `google-genai` SDK
- **Retrieval Engine:** Sub-word character n-gram (1–3) TF-IDF vector space + vehicle fitment boosting + domain synonym dictionary
- **Forecasting Stack:** LightGBM Tweedie count regression, Scikit-learn, Pandas, NumPy
- **Environment Management:** Strict 12-factor configuration via `.env`

---

## 3. Architecture & Core Capabilities

### A. Conversational Dealer Assistant (Part C)
- **Zero Hallucination Grounding:** The assistant has zero static parametric memory of stock or prices. Inquiries trigger `check_stock(sku)`, querying live PostgreSQL inventory at the exact second of inference.
- **Autonomous Tool-Calling Loop:** Gemini autonomously executes `search_parts`, `check_stock`, and `create_draft_order` through typed function declarations. All tool invocations are logged in `tool_calls`.
- **Untrusted Supplier Data Isolation:** Catalogue descriptions written by third-party suppliers are treated strictly as passive data outputs. The system prompt prevents prompt injection overrides embedded within product text.
- **Hybrid Retrieval & Cross-Platform Fitment:**
  - Fast, sub-millisecond character n-gram TF-IDF vectorizer tailored for technical alphanumeric part numbers (`Duke 390`, `BRK-1044`).
  - Integrated `PLATFORM_FAMILIES` matrix recognizing shared Indian 2-wheeler architectures (Royal Enfield J-Series, Yamaha 155 Deltabox, KTM-Bajaj 373cc/200cc). Sibling platform parts receive match boosts and explanatory compatibility notes.
  - Automotive synonym dictionary (`AUTOMOTIVE_SYNONYMS`) mapping colloquial mechanics' slang (e.g., "disc pad" ↔ "brake pad", "blinker" ↔ "led indicator").

### B. Machine Learning Demand Forecasting (Part D)
- **Moving Lunar Diwali Shift:** Demand ramps track the lunar calendar rather than fixed Gregorian dates. Modeled via a dynamic Gaussian proximity kernel centered on pre-festival stocking weeks.
- **Forward Promotional Context:** Leverages supplier forward promotional calendars with dynamic lead and lag indicators.
- **Zero Data Leakage:** Strict temporal cutoff at `2026-09-21`. All features use strictly lagged shifts (`shift(1)` to `shift(52)`) and backward rolling statistics.
- **Two-Stage Hurdle Intermittent Demand Model:** For sparse SKUs with >35% zero-sales weeks (`BRK-1088`, `ELE-1006`, `ELE-1031`, `GRP-1043`), a specialized two-stage model predicts zero occurrence probability $P(Y_t > 0)$ and conditional quantity $\mathbb{E}[Y_t \mid Y_t > 0]$.
- **Hierarchical Demand Reconciliation:** Category-level macro aggregation reconciled against category momentum using regularized proportional scaling.
- **Automated Reorder Planning:** Serves live safety stock ($\lceil 1.65 \times \sigma \times \sqrt{L} \rceil$) and reorder points via `GET /api/reorder-suggestions/`.

### C. Concurrency-Safe Order Engine (Part A2 Bonus)
- **Zero Overselling:** Row-level pessimistic locking (`select_for_update`) prevents inventory races during simultaneous checkout. Stock validation is strictly all-or-nothing.
- **Financial Precision:** 18% statutory GST calculated per line item using `Decimal` and `ROUND_HALF_UP` to the exact paisa, avoiding binary floating-point drift.
- **Order Numbers & Idempotency:** Daily IST-sequenced order numbers (`ORD-YYYYMMDD-NNNN`) and `Idempotency-Key` deduplication.

---

## 4. Forecasting Validation Benchmarks

Model performance backtested on the historical 4-week validation window (`2026-08-24` to `2026-09-14`):

| Model | MAE | RMSE | Error Reduction |
|---|---|---|---|
| Last Value Baseline | 12.394 | 22.319 | — |
| Same Week Last Year (YoY) | 11.425 | 18.187 | — |
| Recent 4-Week Average Baseline | 8.597 | 13.863 | Baseline |
| **LightGBM Multi-Feature Model** | **6.819** | **10.415** | **-20.68% MAE** |

*Predictions committed in `ml/forecast.csv` (160 rows: 40 target SKUs × 4 future weeks).*

---

## 5. Setup & Run Contract

### Environment Setup (`.env`)
Create a `.env` file at the root following `.env.example`:
```env
DATABASE_URL=postgresql://user:password@host:5432/dealerdesk
GEMINI_API_KEY=your_gemini_api_key
SUPPLIER_API_URL=http://127.0.0.1:8099
SUPPLIER_API_KEY=dev-supplier-key
DJANGO_SECRET_KEY=secret-key
```

### Installation & Execution
```bash
# 1. Install dependencies
pip install -r requirements.txt

# 2. Apply database migrations
python manage.py migrate --noinput

# 3. Seed initial catalogue & dealers (atomic bulk upsert, idempotent)
python manage.py seed_data
python manage.py seed_data

# 4. Start development server
python manage.py runserver 0.0.0.0:8000 --noreload
```

---

## 6. Verification & Test Suite

### Automated Contract Smoke Test
```bash
python smoke_test.py --base-url http://localhost:8000 --track aiml
```
*(All 10/10 automated contract checks passing).*

### Django Unit Test Suite
```bash
python manage.py test --keepdb
```
*(All 9/9 business logic and concurrency unit tests passing).*

---

## 7. Assumptions & Design Invariants

1. **Database:** Requires PostgreSQL (version 14+) with standard transaction isolation. Read dynamic connection from `DATABASE_URL`.
2. **Currency Precision:** All monetary amounts are formatted as strings with two decimal places (`"1490.50"`).
3. **Pessimistic Concurrency:** In high-velocity auto parts distribution, pessimistic locking inside short transactions is preferred over optimistic locking to prevent high abort rates.
4. **Temporal Separation:** Training strictly isolates future data using `2026-09-21` as cutoff; future promotional weeks provide only calendar flags.
5. **Safety Stock Service Level:** Safety stock formula targets a 95% cycle service level ($Z = 1.65$) across supplier lead times $L$.
