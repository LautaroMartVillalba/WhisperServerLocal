FROM python:3.11

# RUN apt-get update && apt-get install -y python3.13 python3.13-dev

WORKDIR /app

RUN apt-get update && apt-get install -y \
ffmpeg \
libavformat-dev \
libavcodec-dev \
libavdevice-dev \
libavutil-dev \
libavfilter-dev \
libswresample-dev \
libswresample-dev \
pkg-config

COPY ./requirements.txt ./
RUN pip install -r requirements.txt

COPY ./app ./app/

RUN mkdir -p /tmp/shared_audio && \
    chmod 777 /tmp/shared_audio

EXPOSE 7050

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "7050"]
