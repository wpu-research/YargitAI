# YargitAI API + arayüz. Sunucuda GPU beklenmez: sorgu vektörü CPU'da (~0,3 sn) hesaplanır.
FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 HF_HOME=/data/hf YARGITAI_DB=/data/yargitai.db YARGITAI_VEC=/data/vectors.db
WORKDIR /app
RUN pip install torch==2.6.0 --index-url https://download.pytorch.org/whl/cpu
COPY requirements.txt .
RUN pip install -r requirements.txt pypdf python-docx python-multipart
COPY yargitai ./yargitai
COPY web ./web
COPY scripts ./scripts
EXPOSE 8765
CMD ["sh", "-c", "python -m uvicorn yargitai.api:app --host 0.0.0.0 --port ${PORT:-8765}"]
