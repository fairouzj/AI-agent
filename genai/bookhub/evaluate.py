import os
import sqlite3
from pathlib import Path
import pandas as pd
import matplotlib.pyplot as plt

BASE_DIR = Path(__file__).parent
DATA_DIR = BASE_DIR / "data"
DB_PATH = DATA_DIR / "bookhub.sqlite3"
OUT_DIR = DATA_DIR / "eval"
OUT_DIR.mkdir(parents=True, exist_ok=True)

def load_logs() -> pd.DataFrame:
    conn = sqlite3.connect(DB_PATH)
    df = pd.read_sql_query("SELECT * FROM chat_logs ORDER BY id ASC", conn)
    conn.close()
    return df

def main():
    df = load_logs()
    if df.empty:
        print("No logs yet, use the app and chat a bit, then run evaluate.py again.")
        return

    #counts question by type
    counts = df["question_type"].fillna("unknown").value_counts().reset_index()
    counts.columns = ["question_type", "count"]
    counts.to_csv(OUT_DIR / "question_type_counts.csv", index=False)

    # question types chart
    plt.figure()
    plt.bar(counts["question_type"],counts["count"])
    plt.title("Question Types")
    plt.xlabel("Type")
    plt.ylabel("Count")
    plt.tight_layout()
    plt.savefig(OUT_DIR / "question_types.png", dpi=160)
    plt.close()

    # openai usage and errors over time plot
    df["date"] = df["created_at"].str.slice(0, 10)
    daily = df.groupby("date").agg(
        total=("id", "count"),
        used_openai=("used_openai", "sum"),
        errors=("error", lambda x: (x.fillna("") != "").sum())
    ).reset_index()
    daily.to_csv(OUT_DIR / "daily_metrics.csv", index=False)

    plt.figure()
    plt.plot(daily["date"], daily["used_openai"], label="OpenAI calls")
    plt.plot(daily["date"], daily["errors"], label="Errors")
    plt.title("OpenAI Usage & Errors Over Time")
    plt.xlabel("Date")
    plt.ylabel("Count")
    plt.legend()
    plt.savefig(OUT_DIR / "usage_errors_over_time.png", dpi=160)
    plt.close()
    print("eaved evaluation to:", OUT_DIR)

if __name__ == "__main__":
    main()
