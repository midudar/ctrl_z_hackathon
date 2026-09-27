"""Второй проход OCR по ИД без Colab: те же страницы, что в pages_bundle.zip, распознаются на ноутбуке.

Запасной вариант на случай, когда Colab не даёт GPU («insufficient quota», 24.09). Страница идёт через
ml.pages.read_page(ocr=True) и сразу ложится в кэш OCR — тот же, что пишет fetch_pages.py, поэтому acts.py
его подхватывает, а пересобранный ноутбук Colab готовые страницы пропускает. На CPU ~24 с на страницу.
Прерывать можно в любой момент (Ctrl+C или закрыть окно): при следующем запуске готовые страницы пропускаются.

    python colab/run_pages_local.py                  # все страницы архива по порядку
    python colab/run_pages_local.py --threads 6      # меньше потоков — ноутбук отзывчивее, OCR медленнее
"""
import argparse
import json
import sys
import time
import zipfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
from ml.pages import has_ocr, read_page    # noqa: E402

BUNDLE = HERE / "pages_bundle.zip"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--threads", type=int, default=10, help="потоков torch (из 16 логических ядер)")
    args = ap.parse_args()
    import torch
    torch.set_num_threads(args.threads)

    idx = [json.loads(l) for l in zipfile.ZipFile(BUNDLE).read("pages_index.jsonl").decode("utf-8").splitlines()]
    todo = [r for r in idx if not has_ocr(r["file_id"], r["page"])]
    print(f"страниц в архиве {len(idx)}, уже распознано {len(idx) - len(todo)}, осталось {len(todo)}; "
          f"потоков {args.threads}", flush=True)
    t0, done = time.time(), 0
    for r in todo:
        t = time.time()
        try:
            page = read_page(r["file_id"], r["page"], ocr=True)
        except Exception as e:                      # битая страница не должна останавливать прогон
            print(f"  {r['file_id']} стр.{r['page']}: ошибка {type(e).__name__}: {e}", flush=True)
            continue
        done += 1
        rate = (time.time() - t0) / done
        n_ocr = sum(tk["src"] == "ocr" for tk in page["tokens"])
        print(f"{done}/{len(todo)} {r['file_id']} стр.{r['page']} {r['kind']:8} токенов {n_ocr:4}, "
              f"{time.time() - t:.0f} с; в среднем {rate:.1f} с/стр, осталось ~{rate * (len(todo) - done) / 3600:.1f} ч",
              flush=True)
    print(f"готово: {done} страниц за {(time.time() - t0) / 3600:.1f} ч", flush=True)


if __name__ == "__main__":
    main()
