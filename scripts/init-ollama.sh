#!/bin/bash
set -e

MODEL="${OLLAMA_MODEL:-qwen:7b}"
LOG="/tmp/ollama_init.log"

echo "🚀 [$(date)] Init: Starting Ollama in background..." | tee -a "$LOG"

ollama serve >> "$LOG" 2>&1 &
PID=$!
echo "📋 [$(date)] Ollama PID: $PID, OLLAMA_HOST=${OLLAMA_HOST:-0.0.0.0}" | tee -a "$LOG"

# Ждём появления процесса
echo "⏳ [$(date)] Waiting for Ollama process..." | tee -a "$LOG"
for i in $(seq 1 90); do
  if pgrep -x ollama > /dev/null 2>&1; then
    echo "✅ [$(date)] Ollama process found after $((i*2))s" | tee -a "$LOG"
    break
  fi
  sleep 2
done

# Ждём, пока порт 11434 станет доступен
echo "⏳ [$(date)] Waiting for port 11434..." | tee -a "$LOG"
for i in $(seq 1 60); do
  if bash -c "echo > /dev/tcp/127.0.0.1/11434" 2>/dev/null; then
    echo "✅ [$(date)] Port 11434 is listening" | tee -a "$LOG"
    break
  fi
  sleep 2
done

echo "⬇️ [$(date)] Pulling model: $MODEL" | tee -a "$LOG"
ollama pull "$MODEL" 2>&1 | tee -a "$LOG"

echo "🛑 [$(date)] Stopping init server (PID $PID)..." | tee -a "$LOG"
kill $PID 2>/dev/null || true
wait $PID 2>/dev/null || true

echo "🔄 [$(date)] Starting Ollama in foreground..." | tee -a "$LOG"
# 🔥 exec заменяет текущий процесс — ollama становится PID 1
exec ollama serve
