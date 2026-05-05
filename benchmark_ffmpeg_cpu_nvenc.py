#!/usr/bin/env python3
"""
Benchmark simple CPU vs NVIDIA NVENC transcoding with ffmpeg.

Important:
- This benchmarks re-encoding workflows only.
- If your normal pipeline uses `-c:v copy` (remux), GPU acceleration does not help for video.

Usage examples:
  python benchmark_ffmpeg_cpu_nvenc.py --input-dir C:\videos --pattern "*.mp4"
  python benchmark_ffmpeg_cpu_nvenc.py --input-dir C:\videos --pattern "*.264" --force-format h264
"""

from __future__ import annotations

import argparse
import csv
import re
import shutil
import subprocess
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Optional


@dataclass
class BenchResult:
    file: str
    profile: str
    elapsed_s: float
    ffmpeg_speed_x: Optional[float]
    ok: bool
    error: str


SPEED_RE = re.compile(r"speed=\s*([0-9.]+)x")


def find_ffmpeg(explicit_path: Optional[str]) -> Optional[str]:
    if explicit_path:
        return explicit_path

    if shutil.which("ffmpeg"):
        return "ffmpeg"

    local_app_data = Path.home() / "AppData" / "Local"
    winget_base = local_app_data / "Microsoft" / "WinGet" / "Packages"
    if winget_base.exists():
        matches = list(winget_base.glob("yt-dlp.FFmpeg_*/**/ffmpeg.exe"))
        if matches:
            return str(matches[0])

    return None


def parse_speed(stderr_text: str) -> Optional[float]:
    matches = SPEED_RE.findall(stderr_text)
    if not matches:
        return None
    try:
        return float(matches[-1])
    except ValueError:
        return None


def run_ffmpeg(cmd: list[str]) -> tuple[bool, float, Optional[float], str]:
    t0 = time.perf_counter()
    completed = subprocess.run(cmd, capture_output=True, text=True)
    elapsed = time.perf_counter() - t0
    speed = parse_speed(completed.stderr or "")
    ok = completed.returncode == 0
    error = "" if ok else (completed.stderr or completed.stdout or "ffmpeg failed")[-1200:]
    return ok, elapsed, speed, error


def pick_files(input_dir: Path, pattern: str, limit: int) -> list[Path]:
    files = sorted(input_dir.glob(pattern))
    if limit > 0:
        files = files[:limit]
    return [f for f in files if f.is_file()]


def build_cmd(
    ffmpeg_exe: str,
    src: Path,
    dst: Path,
    profile: str,
    force_format: Optional[str],
    crf: int,
    cq: int,
) -> list[str]:
    cmd = [ffmpeg_exe, "-y"]

    if force_format:
        cmd += ["-f", force_format]

    cmd += ["-i", str(src)]

    if profile == "cpu":
        cmd += [
            "-c:v",
            "libx264",
            "-preset",
            "medium",
            "-crf",
            str(crf),
            "-c:a",
            "copy",
            str(dst),
        ]
    elif profile == "nvenc":
        cmd += [
            "-c:v",
            "h264_nvenc",
            "-preset",
            "p5",
            "-rc",
            "vbr",
            "-cq",
            str(cq),
            "-b:v",
            "0",
            "-c:a",
            "copy",
            str(dst),
        ]
    else:
        raise ValueError(f"Unknown profile: {profile}")

    return cmd


def run_benchmark(
    ffmpeg_exe: str,
    files: Iterable[Path],
    output_dir: Path,
    force_format: Optional[str],
    crf: int,
    cq: int,
) -> list[BenchResult]:
    results: list[BenchResult] = []

    for src in files:
        for profile in ("cpu", "nvenc"):
            out_name = f"{src.stem}.{profile}.mp4"
            dst = output_dir / out_name
            cmd = build_cmd(
                ffmpeg_exe=ffmpeg_exe,
                src=src,
                dst=dst,
                profile=profile,
                force_format=force_format,
                crf=crf,
                cq=cq,
            )

            ok, elapsed, speed_x, error = run_ffmpeg(cmd)
            results.append(
                BenchResult(
                    file=src.name,
                    profile=profile,
                    elapsed_s=elapsed,
                    ffmpeg_speed_x=speed_x,
                    ok=ok,
                    error=error,
                )
            )

    return results


def write_csv(results: list[BenchResult], csv_path: Path) -> None:
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    with open(csv_path, "w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=["file", "profile", "elapsed_s", "ffmpeg_speed_x", "ok", "error"],
        )
        writer.writeheader()
        for row in results:
            writer.writerow(
                {
                    "file": row.file,
                    "profile": row.profile,
                    "elapsed_s": f"{row.elapsed_s:.4f}",
                    "ffmpeg_speed_x": "" if row.ffmpeg_speed_x is None else f"{row.ffmpeg_speed_x:.3f}",
                    "ok": row.ok,
                    "error": row.error,
                }
            )


def print_summary(results: list[BenchResult]) -> None:
    by_file: dict[str, dict[str, BenchResult]] = {}
    for res in results:
        by_file.setdefault(res.file, {})[res.profile] = res

    print("\n=== Summary ===")
    speedups = []
    for file_name, pair in by_file.items():
        cpu = pair.get("cpu")
        gpu = pair.get("nvenc")

        if not cpu or not gpu:
            continue

        if cpu.ok and gpu.ok and gpu.elapsed_s > 0:
            speedup = cpu.elapsed_s / gpu.elapsed_s
            speedups.append(speedup)
            print(
                f"{file_name}: CPU={cpu.elapsed_s:.2f}s | NVENC={gpu.elapsed_s:.2f}s | "
                f"speedup={speedup:.2f}x"
            )
        else:
            print(
                f"{file_name}: CPU ok={cpu.ok} ({cpu.elapsed_s:.2f}s), "
                f"NVENC ok={gpu.ok} ({gpu.elapsed_s:.2f}s)"
            )

    if speedups:
        avg = sum(speedups) / len(speedups)
        print(f"\nAverage speedup (CPU/NVENC): {avg:.2f}x over {len(speedups)} file(s)")
    else:
        print("\nNo valid CPU vs NVENC pair to calculate speedup.")


def main() -> int:
    parser = argparse.ArgumentParser(description="Benchmark ffmpeg CPU (libx264) vs NVENC (h264_nvenc)")
    parser.add_argument("--input-dir", required=True, help="Folder with test videos")
    parser.add_argument("--pattern", default="*.mp4", help="Glob pattern (default: *.mp4)")
    parser.add_argument("--limit", type=int, default=10, help="Max files to benchmark (default: 10)")
    parser.add_argument(
        "--force-format",
        default=None,
        help="Optional ffmpeg input format, e.g. h264 when using raw Annex B streams",
    )
    parser.add_argument("--ffmpeg-path", default=None, help="Path to ffmpeg executable")
    parser.add_argument("--crf", type=int, default=23, help="CRF for CPU libx264 (default: 23)")
    parser.add_argument("--cq", type=int, default=23, help="CQ for NVENC (default: 23)")
    parser.add_argument(
        "--out-csv",
        default="benchmark_cpu_vs_nvenc.csv",
        help="CSV output path (default: benchmark_cpu_vs_nvenc.csv)",
    )
    args = parser.parse_args()

    ffmpeg_exe = find_ffmpeg(args.ffmpeg_path)
    if not ffmpeg_exe:
        print("ERROR: ffmpeg not found. Install ffmpeg or use --ffmpeg-path.")
        return 1

    input_dir = Path(args.input_dir)
    if not input_dir.exists() or not input_dir.is_dir():
        print(f"ERROR: invalid input directory: {input_dir}")
        return 1

    files = pick_files(input_dir=input_dir, pattern=args.pattern, limit=args.limit)
    if not files:
        print(f"ERROR: no files found with pattern '{args.pattern}' in {input_dir}")
        return 1

    print(f"Using ffmpeg: {ffmpeg_exe}")
    print(f"Files selected: {len(files)}")
    print("Note: this benchmark measures re-encoding. Pipelines with '-c:v copy' do not gain GPU speedup.")

    with tempfile.TemporaryDirectory(prefix="ffmpeg_bench_") as tmp:
        out_dir = Path(tmp)
        results = run_benchmark(
            ffmpeg_exe=ffmpeg_exe,
            files=files,
            output_dir=out_dir,
            force_format=args.force_format,
            crf=args.crf,
            cq=args.cq,
        )

    csv_path = Path(args.out_csv)
    write_csv(results, csv_path)
    print_summary(results)
    print(f"\nCSV saved at: {csv_path.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
