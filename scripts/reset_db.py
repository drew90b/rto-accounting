import os
import subprocess
import psycopg2

DB_NAME = "rto_accounting"
DB_USER = "postgres"
DB_PASSWORD = "yourpassword"
DB_HOST = "localhost"
DB_PORT = "5432"

def reset_database():
    print("Connecting to postgres...")

    conn = psycopg2.connect(
        dbname="postgres",
        user=DB_USER,
        password=DB_PASSWORD,
        host=DB_HOST,
        port=DB_PORT,
    )

    conn.autocommit = True
    cur = conn.cursor()

    print("Dropping database if it exists...")
    cur.execute(f"DROP DATABASE IF EXISTS {DB_NAME}")

    print("Creating database...")
    cur.execute(f"CREATE DATABASE {DB_NAME}")

    cur.close()
    conn.close()

    print("Running migrations...")
    subprocess.run(["alembic", "upgrade", "head"])

    print("Loading seed data...")
    subprocess.run(["python", "scripts/seed.py"])

    print("Database reset complete.")

if __name__ == "__main__":
    reset_database()