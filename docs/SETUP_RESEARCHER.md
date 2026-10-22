# Researcher setup

The project owner must provide the repository and assigned service credentials through the team's approved secrets manager. No cloud account or password is created by this code. Researchers should not receive database-owner or cloud-admin credentials.

1. Install Python 3.9+ (3.11+ recommended), then create and activate a virtual environment.
2. Install `requirements.txt`.
3. Copy `.env.example` to `.env` and enter only the credentials assigned to you. Never commit `.env` or share secrets in chat/Git.
4. Install and start Ollama locally. Pull both `qwen2.5:3b` and `nomic-embed-text:latest`; embeddings and question generation run locally.
5. Run the credential-independent tests: `PYTHONPATH=src python -m unittest discover -s tests -v`.
6. Run the read-only checks separately: `check-db`, `check-qdrant`, and `check-storage`.
7. Start Streamlit bound to loopback: `PYTHONPATH=src streamlit run app.py --server.address 127.0.0.1`. The UI is unauthenticated and must not be network-exposed.
8. Upload a PDF, review suggested metadata, choose the inclusive page range, and submit. This one explicit action stores the original, extracts selected pages, creates chunks, generates question candidates, and indexes vectors. Candidates remain pending human review.

The upload requires PostgreSQL, object storage, Qdrant, local Ollama, and current migrations. New inferred company records are marked `pending_review`; they are not treated as verified constituents. A duplicate upload resumes saved downstream work when the job is ready. For operator recovery, use `PYTHONPATH=src python -m cis_fin.cli process-job --job-id JOB_UUID`.

Use a separate migration-capable credential only for owner-reviewed migrations. Do not expose the local Streamlit UI as the shared multi-researcher portal until authentication, role enforcement, and access control are implemented.
