FROM python:3.12-slim

# Tiny image — daemon only needs stdlib (urllib + sqlite3 + json).
# No pip installs required.

WORKDIR /app
COPY daemon.py /app/daemon.py

# Run as a non-privileged numeric UID (no user/group entry required for runtime).
# Avoids the "user already exists" / "group already exists" headache from Debian's
# python:slim shipping with various reserved names.
RUN chown 10000:10000 /app /app/daemon.py
USER 10000:10000

CMD ["python3", "/app/daemon.py"]
