import os
import sqlite3


BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "secureverify.db")


def main():
    if not os.path.exists(DB_PATH):
        raise FileNotFoundError(f"Database not found: {DB_PATH}")

    conn = sqlite3.connect(DB_PATH)

    try:
        conn.execute("PRAGMA foreign_keys = OFF")
        tables = [
            row[0]
            for row in conn.execute(
                """
                SELECT name
                FROM sqlite_master
                WHERE type = 'table' AND name NOT LIKE 'sqlite_%'
                """
            )
        ]

        print(f"This will delete all records in {len(tables)} tables.")
        confirm = input('Type "RESET" to continue: ').strip()
        if confirm != "RESET":
            print("Cancelled; nothing was deleted.")
            return

        with conn:
            conn.execute("BEGIN")
            for table in tables:
                escaped_table = table.replace('"', '""')
                conn.execute(f'DELETE FROM "{escaped_table}"')

            has_sequence = conn.execute(
                """
                SELECT 1
                FROM sqlite_master
                WHERE type = 'table' AND name = 'sqlite_sequence'
                """
            ).fetchone()
            if has_sequence:
                conn.execute("DELETE FROM sqlite_sequence")

        print(f"All records were deleted from {len(tables)} tables.")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
