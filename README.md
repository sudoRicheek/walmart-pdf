# Walmart Order Splitting Dashboard

A Flask-based web application for parsing Walmart order PDFs and splitting costs among roommates.

## Features

- **Parse Walmart Orders**: Import Walmart order data from XLSX files
- **Cost Splitting**: Assign items to roommates and track who owes what
- **Order Filtering**: Filter orders by card (last 4 digits) and date range
- **CSV Export**: Download splits as CSV for easy accounting
- **Persistent Data**: SQLite database maintains splits across sessions

## Project Structure

```
walmart-pdf/
├── app.py              # Flask backend with API endpoints
├── parse_orders.py     # Walmart order XLSX parser
├── requirements.txt    # Python dependencies
├── walmart.db          # SQLite database (auto-created)
├── data/               # Walmart XLSX order files
└── static/
    └── index.html      # Dashboard UI
```

## Installation

1. **Clone the repository:**
   ```bash
   git clone <repo-url>
   cd walmart-pdf
   ```

2. **Create a virtual environment:**
   ```bash
   python3 -m venv .venv
   source .venv/bin/activate  # On Windows: .venv\Scripts\activate
   ```

3. **Install dependencies:**
   ```bash
   pip install -r requirements.txt
   ```

## Usage

### Parse Walmart Orders

1. Place your Walmart order XLSX files in the `data/` directory
2. Run the parser:
   ```bash
   python parse_orders.py
   ```

This will populate the SQLite database with:
- **orders**: Order metadata (date, total, shipping address, payment card)
- **items**: Individual items from each order
- **splits**: Cost allocation per roommate

### Start the Dashboard

```bash
python app.py
```

The dashboard will be available at `http://localhost:5000`

## API Endpoints

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/api/orders?card=&date_from=&date_to=` | List orders with optional filters |
| `GET` | `/api/orders/<id>/items` | Get items for an order with split checkboxes |
| `PUT` | `/api/splits/<item_id>` | Toggle roommate assignment for an item |
| `GET` | `/api/summary?card=&date_from=&date_to=` | Per-roommate cost totals |
| `GET` | `/api/cards` | List distinct card last-4 digits |
| `GET` | `/api/export/csv?card=&date_from=&date_to=` | Download splits as CSV |
| `GET` | `/` | Serve dashboard UI |

## Database Schema

### orders
- `id` (INTEGER PRIMARY KEY)
- `order_number` (TEXT)
- `order_date` (TEXT)
- `shipping_address` (TEXT)
- `payment_last4` (TEXT)
- `subtotal`, `delivery_charges`, `tax`, `tip`, `order_total` (REAL)

### items
- `id` (INTEGER PRIMARY KEY)
- `order_id` (FK → orders)
- `product_name` (TEXT)
- `quantity` (INTEGER)
- `price` (REAL)
- `delivery_status` (TEXT)
- `product_link` (TEXT)

### splits
- `id` (INTEGER PRIMARY KEY)
- `item_id` (FK → items)
- `roommate` (TEXT)
- `checked` (BOOLEAN)

### roommates
- `id` (INTEGER PRIMARY KEY)
- `name` (TEXT)
- `sort_order` (INTEGER)
