FROM nvidia/cuda:12.6.3-cudnn-runtime-ubuntu22.04

ENV DEBIAN_FRONTEND=noninteractive
ENV PYTHONUNBUFFERED=1
ENV PIP_NO_CACHE_DIR=1

RUN apt-get update && apt-get install -y \
    python3 \
    python3-pip \
    python3-dev \
    git \
    libgl1 \
    libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

RUN ln -sf /usr/bin/python3 /usr/bin/python

WORKDIR /app

COPY requirements.txt .

RUN python -m pip install --upgrade pip

RUN python -m pip install \
    paddlepaddle-gpu==3.2.1 \
    -i https://www.paddlepaddle.org.cn/packages/stable/cu126/

RUN python -m pip install \
    "paddleocr[doc-parser]>=3.6.0"

RUN python -m pip install \
    https://paddle-whl.bj.bcebos.com/nightly/cu126/safetensors/safetensors-0.6.2.dev0-cp38-abi3-linux_x86_64.whl

RUN python -m pip install -r requirements.txt

COPY app.py .

EXPOSE 7860

CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "7860"]