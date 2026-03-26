# Solvency — Smart Budget & Expense Tracking

A full-stack budget tracking web app with SMS parsing, budget simulation, fraud/waste detection, and a neon-themed dark UI. Track expenses, simulate savings, and get smart insights—all running locally with no paid APIs.

## Features

### Core
- **Dashboard**: Total expenses, category-wise spending, monthly budget vs spent
- **SMS Parsing**: Extracts amount, merchant, date, payment mode, category from bank SMS
- **Spending Personality**: Saver, Balanced, or Impulsive based on % of income spent
- **Predictions**: End-of-month expense prediction, repeated merchant detection

### New Enhancements
- **Budget Simulator**: Input natural language like *"reduce food by 100 per day"* → see projected monthly savings
- **Budget Warning**: Alert when spending exceeds 80% of budget
- **Fraud/Waste Detector**: Flags small repeated transactions under ₹100 (potential leaks or fraud)
- **Expandable Panels**: Click any section to expand/collapse for a cleaner, focused view

### UI
- Dark theme with neon accents (cyan, magenta, lime, purple)
- Click-to-expand panels for organized navigation
- Improved charts with neon styling
- Mobile-friendly layout

## Tech Stack

- **Frontend**: HTML, CSS, JavaScript (Chart.js)
- **Backend**: Python Flask
- **Database**: SQLite (no setup required)

## How to Run Locally

```bash
cd Desktop\budget-tracker-sms
pip install -r requirements.txt
python app.py
```

Open [http://127.0.0.1:5000](http://127.0.0.1:5000)

### Simulate SMS Input

Paste bank SMS into the textbox. Examples:

```
INR 450 spent on ZOMATO via UPI on 03-02-26. Bal: 5230
Rs 1200 debited for AMAZON on 02-02-2026
```

### Budget Simulator

Try commands like:
- `reduce food by 100 per day`
- `cut shopping by 500 per week`
- `save 200 daily on travel`

## API Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/` | GET | Dashboard UI |
| `/api/receive_sms` | POST | Accept `{"message": "..."}` and store transaction |
| `/api/parse_only` | POST | Parse SMS without storing |
| `/api/dashboard` | GET | Full dashboard data (includes warnings, fraud alerts) |
| `/api/simulate` | POST | Budget simulation: `{"command": "reduce food by 100 per day"}` |
| `/api/transactions` | GET | Recent transactions |
| `/api/settings` | GET/POST | Budget and income settings |

## Categories (Auto-Detected)

- **Food**: Zomato, Swiggy, grocery, cafes
- **Travel**: Uber, Ola, IRCTC, fuel
- **Shopping**: Amazon, Flipkart, Myntra
- **Bills**: Recharge, electricity, subscriptions
- **Other**: Default

## Spending Personality

- **Saver**: &lt; 50% of income spent
- **Balanced**: 50–75%
- **Impulsive**: &gt; 75%

## No External APIs

All logic runs locally. No paid services or cloud APIs.
