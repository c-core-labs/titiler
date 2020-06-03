FROM python:3.7.7-buster as builder

# Install the C compiler tools
RUN apt-get update
RUN apt-get install -y build-essential cmake wget git pkg-config
RUN pip install --upgrade pip

RUN mkdir /install
WORKDIR /install
COPY requirements.txt /requirements.txt
RUN pip install --prefix=/install -r /requirements.txt

FROM python:3.7.7-slim as base
COPY --from=builder /install /usr/local
COPY titiler /titiler

CMD ["uvicorn", "titiler.main:app", "--host", "0.0.0.0", "--port", "8080"]
