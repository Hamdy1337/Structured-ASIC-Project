import argparse
import csv
from pathlib import Path
from typing import List, Dict, Any
import math
import numpy as np
import matplotlib.pyplot as plt


def _read_csv(path: str) -> List[Dict[str, Any]]:
    rows = []
    with open(path, newline="") as f:
        reader = csv.DictReader(f)
        for r in reader:
            rows.append(r)
    return rows


def _to_float(v: Any, default: float = math.nan) -> float:
    try:
        return float(v)
    except Exception:
        return default


def moving_average(x: np.ndarray, window: int) -> np.ndarray:
    if window <= 1 or window > len(x):
        return x
    cumsum = np.cumsum(np.insert(x, 0, 0.0))
    return (cumsum[window:] - cumsum[:-window]) / window


def plot_full(csv_rows: List[Dict[str, Any]], out_prefix: Path, ma: int):
    eps = np.array([int(r['episode']) for r in csv_rows])
    loss = np.array([_to_float(r['loss']) for r in csv_rows])
    pl = np.array([_to_float(r['policy_loss']) for r in csv_rows])
    vl = np.array([_to_float(r['value_loss']) for r in csv_rows])
    ent = np.array([_to_float(r['entropy']) for r in csv_rows])
    hpwl = np.array([_to_float(r['hpwl_end']) for r in csv_rows])

    fig, ax = plt.subplots(2, 2, figsize=(10, 7))
    ax[0,0].plot(eps, loss, label='loss', alpha=0.4)
    ax[0,0].plot(eps[ma-1:], moving_average(loss, ma), label=f'loss(ma{ma})')
    ax[0,0].set_title('Full Placer Loss'); ax[0,0].legend(); ax[0,0].grid(True, alpha=0.3)

    ax[0,1].plot(eps, pl, label='policy', alpha=0.5)
    ax[0,1].plot(eps, vl, label='value', alpha=0.5)
    ax[0,1].plot(eps, ent, label='entropy', alpha=0.5)
    ax[0,1].set_title('Components'); ax[0,1].legend(); ax[0,1].grid(True, alpha=0.3)

    ax[1,0].plot(eps, hpwl, label='hpwl')
    if len(hpwl) >= ma:
        ax[1,0].plot(eps[ma-1:], moving_average(hpwl, ma), label=f'hpwl(ma{ma})')
    ax[1,0].set_title('Episode End HPWL (subset)'); ax[1,0].legend(); ax[1,0].grid(True, alpha=0.3)

    ax[1,1].plot(eps, ent, label='entropy', color='darkorange')
    ax[1,1].set_title('Entropy'); ax[1,1].legend(); ax[1,1].grid(True, alpha=0.3)

    fig.tight_layout()
    out_file = out_prefix.with_suffix('.full.png')
    fig.savefig(out_file, dpi=140)
    print(f'Saved full placer plot: {out_file}')


def plot_swap(csv_rows: List[Dict[str, Any]], out_prefix: Path, ma: int):
    eps = np.array([int(r['episode']) for r in csv_rows])
    loss = np.array([_to_float(r['loss']) for r in csv_rows])
    hpwl = np.array([_to_float(r['hpwl_local_end']) for r in csv_rows])

    fig, ax = plt.subplots(1, 2, figsize=(10, 4))
    ax[0].plot(eps, loss, label='loss', alpha=0.4)
    if len(loss) >= ma:
        ax[0].plot(eps[ma-1:], moving_average(loss, ma), label=f'loss(ma{ma})')
    ax[0].set_title('Swap Refiner Loss'); ax[0].legend(); ax[0].grid(True, alpha=0.3)

    ax[1].plot(eps, hpwl, label='local hpwl', alpha=0.7)
    if len(hpwl) >= ma:
        ax[1].plot(eps[ma-1:], moving_average(hpwl, ma), label=f'hpwl(ma{ma})')
    ax[1].set_title('Local HPWL (touched nets)'); ax[1].legend(); ax[1].grid(True, alpha=0.3)

    fig.tight_layout()
    out_file = out_prefix.with_suffix('.swap.png')
    fig.savefig(out_file, dpi=140)
    print(f'Saved swap refiner plot: {out_file}')


def main():
    ap = argparse.ArgumentParser(description='Plot PPO training metrics for RL placer.')
    ap.add_argument('--full-log-csv', default=None, help='CSV produced by full placer PPO')
    ap.add_argument('--swap-log-csv', default=None, help='CSV produced by swap refiner PPO')
    ap.add_argument('--out-prefix', default='build/rl_training')
    ap.add_argument('--ma-window', type=int, default=10, help='Moving average window')
    args = ap.parse_args()

    out_prefix = Path(args.out_prefix)
    out_prefix.parent.mkdir(parents=True, exist_ok=True)

    if args.full_log_csv and Path(args.full_log_csv).exists():
        rows = _read_csv(args.full_log_csv)
        rows_full = [r for r in rows if r.get('kind') == 'full'] or rows  # backward compatibility
        plot_full(rows_full, out_prefix, args.ma_window)
    else:
        print('Full log CSV not provided or missing; skipping full plot.')

    if args.swap_log_csv and Path(args.swap_log_csv).exists():
        rows = _read_csv(args.swap_log_csv)
        rows_swap = [r for r in rows if r.get('kind') == 'swap'] or rows
        plot_swap(rows_swap, out_prefix, args.ma_window)
    else:
        print('Swap log CSV not provided or missing; skipping swap plot.')


if __name__ == '__main__':
    main()
