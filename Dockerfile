# syntax=docker/dockerfile:1

ARG PYTHON_VERSION=3.12
FROM python:${PYTHON_VERSION}-slim

LABEL org.opencontainers.image.title="Logical Infrastructure Manager" \
      org.opencontainers.image.description="LIM application foundation" \
      org.opencontainers.image.source="https://github.com/betin1975/logical-infrastructure-manager"

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /opt/lim

RUN groupadd --gid 10001 lim \
    && useradd --uid 10001 --gid lim --no-create-home --shell /usr/sbin/nologin lim

RUN apt-get update \
    && apt-get install --yes --no-install-recommends openssh-client \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt ./
RUN python -m pip install --requirement requirements.txt

COPY --chown=lim:lim app ./app
COPY --chown=lim:lim config/default.yml ./config/default.yml

RUN mkdir -p runtime/data runtime/jobs runtime/logs runtime/backups ssh \
    && chown -R lim:lim runtime \
    && chown root:lim ssh \
    && chmod 0750 ssh

USER lim

# One-shot startup validates local SSH configuration without network access.
CMD ["python", "-m", "app"]
