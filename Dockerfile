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

COPY ./.env ./
COPY ./app ./app/

EXPOSE 7050

CMD ["python", "-m", "app.main"]
