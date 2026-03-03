#!/bin/bash
# run_sentiment.sh — Ejecuta analyze_sentiment.py dentro del contenedor Docker
# Uso: ./run_sentiment.sh --symbols BTC/USDT --timeframes 4h,1d --telegram

sudo docker run --rm --env-file .env -v $(pwd):/app motor-trading \
  python analyze_sentiment.py "$@"
