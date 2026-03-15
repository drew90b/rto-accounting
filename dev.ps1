.\venv\Scripts\activate.ps1
alembic upgrade head
uvicorn app.main:app --reload