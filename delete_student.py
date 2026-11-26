import os
import sqlite3


BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "secureverify.db")


def main():
    usn = input("Enter the USN to delete: ").strip().upper()

    if not os.path.exists(DB_PATH):
        raise FileNotFoundError(f"Database not found: {DB_PATH}")

    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("PRAGMA foreign_keys = ON")

        student = conn.execute(
            "SELECT id, name FROM students WHERE usn = ?",
            (usn,),
        ).fetchone()

        if student is None:
            print(f"No student found with USN {usn}.")
            return

        print(f"Found: {student[1]} ({usn})")
        confirm = input(f"Type the USN {usn} to confirm: ").strip().upper()

        if confirm == usn:
            conn.execute("DELETE FROM students WHERE usn = ?", (usn,))
            print("Student and related records deleted.")
        else:
            print("Cancelled; nothing was deleted.")


if __name__ == "__main__":
    main()
