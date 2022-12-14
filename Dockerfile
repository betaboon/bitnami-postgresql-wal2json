ARG POSTGRESQL_TAG
FROM docker.io/bitnami/postgresql:${POSTGRESQL_TAG} as builder

ARG WAL2JSON_TAG

USER root
RUN apt-get update && apt-get install -y gcc git make && rm -rf /var/lib/apt/lists/*
RUN git clone https://github.com/eulerto/wal2json.git --branch ${WAL2JSON_TAG}
RUN cd wal2json && USE_PGXS=1 make && USE_PGXS=1 make install

FROM docker.io/bitnami/postgresql:${POSTGRESQL_TAG}
COPY --from=builder /opt/bitnami/postgresql/lib/wal2json.so /opt/bitnami/postgresql/lib/
