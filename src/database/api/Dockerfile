FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app
COPY database/api/requirements.txt ./requirements.txt
RUN apt-get update \
    && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends tzdata \
    && rm -rf /var/lib/apt/lists/* \
    && pip install --no-cache-dir -r requirements.txt \
    && useradd --create-home appuser \
    && mkdir -p /var/lib/grok-sp500 /var/log/gieda \
    && chown appuser:appuser /var/lib/grok-sp500 /var/log/gieda

COPY database/api/app.py ./app.py
COPY database/api/ai_verification.py ./ai_verification.py
COPY database/api/error_file_logging.py ./error_file_logging.py
COPY database/api/candle_prompt_context.py ./candle_prompt_context.py
COPY database/api/prompt_templates.py ./prompt_templates.py
COPY database/api/signal_potential_calculator.py ./signal_potential_calculator.py
COPY database/api/market_products.py ./market_products.py
COPY database/api/sound_alert.py ./sound_alert.py
COPY database/api/strategy_context_registry.py ./strategy_context_registry.py
COPY database/api/transaction_tracker.py ./transaction_tracker.py
COPY algorithms/Chat-gpt6/EURUSD/ ./algorithm/
COPY algorithms/Grok-45/EURUSD/EURUSDStrategyV2.py ./grok_algorithm/
COPY algorithms/Claude-sonnet55/EURUSD/ ./claude_eurusd_algorithm/
COPY algorithms/Claude-sonnet55/DAX40/ ./dax_algorithm/
COPY algorithms/Chat-gpt6/DAX40/ ./chat_dax_algorithm/
COPY algorithms/Chat-gpt6/USDMXN/ ./usdmxn_algorithm/
COPY algorithms/Chat-gpt6/USDJPY/ ./usdjpy_algorithm/
COPY algorithms/Claude-sonnet55/USDJPY/ ./claude_usdjpy_algorithm/
COPY algorithms/Grok-45/USDMXN/ ./grok_usdmxn_algorithm/
COPY algorithms/Claude-sonnet55/S&P500/ ./sp500_algorithm/
COPY algorithms/Grok-45/S&P500/ ./grok_sp500_algorithm/
COPY algorithms/Chat-gpt6/XAUUSD/ ./xauusd_algorithm/
COPY algorithms/Claude-sonnet55/XAUUSD/ ./claude_xauusd_algorithm/
USER appuser
ENV HOME=/home/appuser

EXPOSE 8000
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000"]
