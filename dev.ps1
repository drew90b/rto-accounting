.\venv\Scripts\activate
alembic upgrade head
uvicorn app.main:app --reload