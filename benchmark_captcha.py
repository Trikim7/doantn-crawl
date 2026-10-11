"""Đo OCR offline trên ảnh đã gán nhãn bằng mắt; không truy cập mạng."""
import argparse
import json
import time
from pathlib import Path

from PIL import Image
import iuh_telegram_bot as bot


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--all", action="store_true", help="Đo cả ảnh tạo nền")
    args = parser.parse_args()
    folder = bot.HERE / "tests/fixtures/captcha"
    labels = json.loads((folder / "labels.json").read_text())
    rows = []
    for sample in labels:
        if sample["split"] != "evaluation" and not args.all:
            continue
        path = folder / sample["file"]
        row = dict(sample)
        for method, run in (
            ("old", lambda: bot.raw_captcha_candidates(Image.open(path).convert("L"))),
            ("new", lambda: bot.captcha_candidates(path.read_bytes())),
        ):
            start = time.perf_counter()
            candidates = run()
            ambiguous = len(candidates) > 1 and candidates[0][1] == candidates[1][1]
            guess = candidates[0][0] if candidates and not ambiguous else None
            row[method] = dict(guess=guess, correct=guess == sample["label"],
                               candidates=candidates, seconds=round(time.perf_counter() - start, 3))
        rows.append(row)
        print(sample["file"], "nhãn:", sample["label"], "cũ:", row["old"]["guess"],
              "mới:", row["new"]["guess"], flush=True)
    for method in ("old", "new"):
        correct = sum(row[method]["correct"] for row in rows)
        print(f"{method}: {correct}/{len(rows)} ảnh đúng hoàn toàn")
    result = bot.HERE / "captcha_benchmark_results.json"
    result.write_text(json.dumps(rows, ensure_ascii=False, indent=2) + "\n")
    print("Đã lưu:", result)


if __name__ == "__main__":
    main()
