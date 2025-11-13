# Currency Conversion API

This repository provides a simple REST service that converts amounts
between world currencies. It is built with [FastAPI](https://fastapi.tiangolo.com/)
and consumes the open‑source [Exchange‑API](https://github.com/fawazahmed0/exchange-api)
maintained by Fawaz Ahmed. The upstream dataset includes rates for
over 200 currencies, cryptocurrencies and metals, published daily and
served via jsDelivr. No API key or account is required.

## Features

* **Instant conversion** – convert any amount between any two supported
  currencies using a single HTTP call.
* **Free and unlimited** – there are no formal rate limits; the data is
  served from a CDN. Please be respectful and consider caching.
* **200+ currencies supported** – includes major world currencies,
  cryptocurrencies, and precious metals.
* **6‑hour in‑memory cache** – the service caches upstream rates for
  six hours to minimize latency and redundant network calls. The
  underlying dataset updates daily.
* **Pretty‑printed JSON** – all responses are indented for human
  readability.

## Installation

1. Clone this repository and navigate into it:

   ```bash
   git clone https://github.com/your‑username/currency‑conversion‑api.git
   cd currency‑conversion‑api
   ```

2. (Optional but recommended) create and activate a Python virtual
   environment:

   ```bash
   python3 -m venv venv
   source venv/bin/activate
   ```

3. Install the dependencies:

   ```bash
   pip install -r currency_api/requirements.txt
   ```

4. Start the development server:

   ```bash
   uvicorn currency_api.main:app --reload --host 0.0.0.0 --port 8000
   ```

The API will be available at `http://localhost:8000/`. FastAPI also
generates interactive documentation at `/docs` and `/redoc`.

## Usage

### Get a conversion rate

Retrieve the exchange rate from one currency to another. The query
parameters are `from` (base currency) and `to` (target currency). Both
must be ISO currency codes (e.g. `USD`, `NGN`).

```
GET /rate?from=USD&to=NGN
```

Example response:

```json
{
  "from": "USD",
  "to": "NGN",
  "rate": 1630.234
}
```

### Convert an amount

Convert a specific amount from one currency to another. Query
parameters:

* `from` – base currency ISO code (e.g. `EUR`)
* `to` – target currency ISO code (e.g. `USD`)
* `amount` – numeric amount to convert

```
GET /convert?from=EUR&to=USD&amount=100
```

Example response:

```json
{
  "from": "EUR",
  "to": "USD",
  "amount": 100.0,
  "rate": 1.0945,
  "converted_amount": 109.45
}
```

### Error handling

If either currency code is unsupported, the service returns a
`400 Bad Request` with an informative error message. Other HTTP
errors from the upstream API (e.g. network failures) are propagated as
`400` errors.

## Deployment

This service is lightweight and can run anywhere Python is supported.
Below is an example `Dockerfile` to build a container image:

```dockerfile
FROM python:3.11-slim
WORKDIR /app
COPY currency_api/ ./currency_api/
COPY currency_api/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt
CMD ["uvicorn", "currency_api.main:app", "--host", "0.0.0.0", "--port", "8000"]
```

After building the image with `docker build -t currency-api .`, you can
run it using `docker run -p 8000:8000 currency-api`.

On platform‑as‑a‑service providers like Render or Railway, point the
entry command to `uvicorn currency_api.main:app --host 0.0.0.0 --port $PORT`.

## Acknowledgements

* The underlying exchange rates are provided by the
  [Exchange‑API](https://github.com/fawazahmed0/exchange-api) project,
  which releases its data under the CC0-1.0 license.
* This project itself is released under the MIT License (see
  [LICENSE](LICENSE)).
