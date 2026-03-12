Remove-Item storage\*.db -ErrorAction Ignore
alembic upgrade head