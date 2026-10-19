# EcommAI

A shopping agent for India. Say what you want, what it must have and your budget. EcommAI finds products, checks every must-have on every product (photo, details and reviews) and shows the proof.

- [`PLAN.md`](PLAN.md): what we're building and why.
- [`IMPLEMENTATION.md`](IMPLEMENTATION.md): how to build it, step by step, with tests and checks.

## Run it locally

You need [uv](https://docs.astral.sh/uv/) and Node.js 20 or newer.

```bash
cp .env.example .env        # then fill in the values
uv sync                     # installs Python 3.12+ packages into .venv
uv run pytest               # fast tests, no cloud calls
uv run pytest -m live       # tests that call Google Cloud or Kaggle
```

The web app lives in `web/` (see `web/README.md`).

## Data

Product photos and basic details come from the Kaggle [Fashion Product Images Dataset](https://www.kaggle.com/datasets/paramaggarwal/fashion-product-images-dataset) by Param Aggarwal. Prices, stock, delivery, cash on delivery, returns and reviews are **demo data** that we generate, and the app labels them as demo data.
