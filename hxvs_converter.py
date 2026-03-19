#!/usr/bin/env python3
"""
hxvs_converter.py
-----------------
Converts HiP2P / HiSilicon HXVS proprietary .264 files to .mp4 or .avi
without requiring Converter.exe.

Container format discovered by reverse engineering:
  File header  : "HXVS" + width(uint16LE) + 2 bytes pad + height(uint16LE) + 6 bytes pad  = 16 bytes
  Frame blocks : repeated until EOF
    "HXVF" video frame : magic(4) + payload_size(uint32LE) + timestamp_ms(uint32LE) + flags(uint32LE) + payload
    "HXAF" audio frame : magic(4) + payload_size(uint32LE) + timestamp_ms(uint32LE) + flags(uint32LE) + 4-byte sub-header + 160 bytes G.711 A-law
    "HXFI" info  frame : skip entirely
  flags : 1 = I-frame, 2 = P-frame (audio always 0)
  Audio sub-header (4 bytes before raw A-law data) is discarded.

FPS derivation: total_video_frames / ((last_video_ts - first_video_ts) / 1000)
  Reproduces Converter.exe behaviour (results in ~13 fps for test files).

Usage:
  python hxvs_converter.py input.264 [output.mp4]
  python hxvs_converter.py --batch input_dir/ output_dir/

Requires: ffmpeg in PATH or --ffmpeg-path flag.
"""

import argparse
import csv
import os
import struct
import subprocess
import sys
import tempfile
import logging
from pathlib import Path
from typing import Optional

logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s')
log = logging.getLogger(__name__)

HXVS = b'HXVS'
HXVF = b'HXVF'
HXAF = b'HXAF'
AUDIO_SUBHEADER_SIZE = 4
AUDIO_SAMPLE_RATE = 8000


def parse_hxvs(data: bytes):
    """
    Parse an HXVS .264 file.
    Returns: (width, height, video_frames, audio_frames)
      video_frames: list of (timestamp_ms, flags, payload_bytes)
      audio_frames: list of (timestamp_ms, raw_alaw_bytes)
    """
    if data[:4] != HXVS:
        raise ValueError(f"Not an HXVS file (magic: {data[:4].hex()})")

    width  = struct.unpack_from('<H', data, 4)[0]
    height = struct.unpack_from('<H', data, 8)[0]
    log.info(f"HXVS header: {width}x{height}")

    offset = 16
    video_frames = []
    audio_frames = []
    skipped = 0

    while offset + 16 <= len(data):
        magic = data[offset:offset+4]
        size  = struct.unpack_from('<I', data, offset+4)[0]
        ts    = struct.unpack_from('<I', data, offset+8)[0]
        flags = struct.unpack_from('<I', data, offset+12)[0]

        if magic == HXVF:
            payload = data[offset+16:offset+16+size]
            video_frames.append((ts, flags, payload))
        elif magic == HXAF:
            # Strip 4-byte sub-header, keep raw G.711 A-law
            raw = data[offset+16+AUDIO_SUBHEADER_SIZE:offset+16+size]
            audio_frames.append((ts, raw))
        elif magic in (b'HXFI', b'HXFC'):
            pass  # info/config frames, skip
        else:
            # Unknown frame type — try to resync to next known magic
            nxt_v = data.find(HXVF, offset+1)
            nxt_a = data.find(HXAF, offset+1)
            candidates = [x for x in [nxt_v, nxt_a] if x != -1]
            if not candidates:
                log.warning(f"Cannot resync at offset {hex(offset)}, stopping parse")
                break
            nxt = min(candidates)
            log.debug(f"Skipping unknown magic {magic.hex()} at {hex(offset)}, resyncing to {hex(nxt)}")
            skipped += nxt - offset
            offset = nxt
            continue

        offset += 16 + size

    if skipped:
        log.warning(f"Skipped {skipped} bytes due to unknown frame types")

    return width, height, video_frames, audio_frames


def count_encoded_frames(video_frames: list) -> int:
    """
    Count actual encoded video frames (IDR + P-slices), excluding parameter sets.
    NAL type 7 = SPS, NAL type 8 = PPS — these do NOT consume a timestamp slot.
    """
    count = 0
    for _, _, payload in video_frames:
        # First start code is at payload[:4], NAL header byte follows
        # Payload may start with 00 00 00 01 (4 bytes) or 00 00 01 (3 bytes)
        if payload[:3] == b'\x00\x00\x01':
            nal_byte = payload[3]
        elif payload[:4] == b'\x00\x00\x00\x01':
            nal_byte = payload[4] if len(payload) > 4 else 0
        else:
            nal_byte = 0
        nal_type = nal_byte & 0x1F
        # Skip parameter sets (SPS=7, PPS=8) and AUDs (9)
        if nal_type not in (7, 8, 9):
            count += 1
    return count


def compute_fps(video_frames):
    """
    Compute fps from TS range / encoded-frame count.
    Excludes SPS/PPS HXVF entries from the frame count so duration is accurate.
    e.g. 7502 actual frames / 600.08s = 12.5 fps (not 13 from raw HXVF count).
    """
    if len(video_frames) < 2:
        return 25.0  # fallback
    ts_first = video_frames[0][0]
    ts_last  = video_frames[-1][0]
    duration_s = (ts_last - ts_first) / 1000.0
    if duration_s <= 0:
        return 25.0
    encoded = count_encoded_frames(video_frames)
    fps = encoded / duration_s
    log.info(
        f"FPS computed: {encoded} encoded frames (of {len(video_frames)} HXVF) "
        f"/ {duration_s:.3f}s = {fps:.4f} fps"
    )
    return fps


def convert(input_path: Path, output_path: Path, ffmpeg_exe: str = 'ffmpeg',
            container: str = 'mp4', overwrite: bool = False):
    """
    Full pipeline: parse HXVS → extract streams → call ffmpeg to mux.
    """
    if output_path.exists() and not overwrite:
        log.error(f"Output exists: {output_path}  (use --overwrite to replace)")
        return False

    log.info(f"Reading {input_path} ({input_path.stat().st_size:,} bytes)")
    data = input_path.read_bytes()

    width, height, video_frames, audio_frames = parse_hxvs(data)
    fps = compute_fps(video_frames)
    # Express fps as an exact fraction for ffmpeg (e.g. 12.5 → 25/2, 13.0 → 13/1)
    from fractions import Fraction
    fps_frac = Fraction(fps).limit_denominator(100)
    fps_str = f"{fps_frac.numerator}/{fps_frac.denominator}"
    log.info(f"Using fps flag: -r {fps_str}")

    log.info(f"Video frames: {len(video_frames)}  Audio chunks: {len(audio_frames)}")

    with tempfile.TemporaryDirectory() as tmpdir:
        vid_path = os.path.join(tmpdir, "video.264")
        aud_path = os.path.join(tmpdir, "audio.alaw")

        # Write raw Annex B H.264
        log.info(f"Writing Annex B stream ({sum(len(f[2]) for f in video_frames):,} bytes)")
        with open(vid_path, 'wb') as vf:
            for _, _, payload in video_frames:
                vf.write(payload)

        has_audio = len(audio_frames) > 0
        if has_audio:
            log.info(f"Writing G.711 A-law audio ({sum(len(f[1]) for f in audio_frames):,} bytes)")
            with open(aud_path, 'wb') as af:
                for _, raw in audio_frames:
                    af.write(raw)

        # Build ffmpeg command
        cmd = [
            ffmpeg_exe,
            '-y' if overwrite else '-n',
            '-f', 'h264',
            '-r', fps_str,
            '-i', vid_path,
        ]

        if has_audio:
            cmd += [
                '-f', 'alaw',
                '-ar', str(AUDIO_SAMPLE_RATE),
                '-ac', '1',
                '-i', aud_path,
            ]

        # Video: copy Annex B stream as-is
        cmd += ['-c:v', 'copy', '-map', '0:v:0']

        if has_audio:
            cmd += ['-map', '1:a:0']
            if container == 'mp4':
                # MP4 does not support pcm_alaw; transcode to AAC
                cmd += ['-c:a', 'aac', '-b:a', '64k', '-ar', '8000']
                log.info("MP4 container: transcoding G.711 A-law → AAC 64k")
            else:
                # AVI supports pcm_alaw natively (codec tag 0x0006)
                cmd += ['-c:a', 'copy']

        if container == 'avi':
            cmd += ['-vtag', 'H264']

        cmd.append(str(output_path))

        log.info(f"Running: {' '.join(cmd)}")
        result = subprocess.run(cmd, capture_output=True, text=True)

        if result.returncode != 0:
            log.error("ffmpeg failed:\n" + result.stderr[-3000:])
            return False

        log.info(f"Done: {output_path} ({output_path.stat().st_size:,} bytes)")
        return True


def extract_audio_only(input_path: Path, output_path: Path, ffmpeg_exe: str = 'ffmpeg',
                       audio_format: str = 'wav', audio_sample_rate: int = 16000,
                       audio_channels: int = 1, overwrite: bool = False):
    """
    Extract audio from HXAF frames.
    - alaw: writes raw G.711 A-law bytes
    - wav:  transcodes A-law -> PCM s16le at target sample rate/channels
    - flac: transcodes A-law -> FLAC at target sample rate/channels
    """
    if output_path.exists() and not overwrite:
        log.error(f"Output exists: {output_path}  (use --overwrite to replace)")
        return False

    log.info(f"Reading {input_path} ({input_path.stat().st_size:,} bytes)")
    data = input_path.read_bytes()
    _, _, _, audio_frames = parse_hxvs(data)

    if not audio_frames:
        log.error("No HXAF audio frames found")
        return False

    output_path.parent.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory() as tmpdir:
        raw_alaw_path = os.path.join(tmpdir, "audio.alaw")
        with open(raw_alaw_path, 'wb') as audio_file:
            for _, raw in audio_frames:
                audio_file.write(raw)

        if audio_format == 'alaw':
            with open(raw_alaw_path, 'rb') as source_handle, open(output_path, 'wb') as target_handle:
                target_handle.write(source_handle.read())
            log.info(f"Done: {output_path} ({output_path.stat().st_size:,} bytes)")
            return True

        cmd = [
            ffmpeg_exe,
            '-y' if overwrite else '-n',
            '-f', 'alaw',
            '-ar', str(AUDIO_SAMPLE_RATE),
            '-ac', '1',
            '-i', raw_alaw_path,
            '-ar', str(audio_sample_rate),
            '-ac', str(audio_channels),
        ]

        if audio_format == 'wav':
            cmd += ['-c:a', 'pcm_s16le']
        elif audio_format == 'flac':
            cmd += ['-c:a', 'flac']
        else:
            log.error(f"Unsupported audio format: {audio_format}")
            return False

        cmd.append(str(output_path))

        log.info(f"Running: {' '.join(cmd)}")
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            log.error("ffmpeg failed:\n" + result.stderr[-3000:])
            return False

        log.info(f"Done: {output_path} ({output_path.stat().st_size:,} bytes)")
        return True


def batch_convert(input_dir: Path, output_dir: Path, ffmpeg_exe: str = 'ffmpeg',
                  container: str = 'mp4', overwrite: bool = False):
    """Convert all .264 files in input_dir."""
    output_dir.mkdir(parents=True, exist_ok=True)
    files = sorted(input_dir.glob("*.264"))
    if not files:
        log.warning(f"No .264 files found in {input_dir}")
        return

    ok, fail = 0, 0
    for src in files:
        dst = output_dir / (src.stem + f".{container}")
        log.info(f"[{ok+fail+1}/{len(files)}] {src.name} -> {dst.name}")
        if convert(src, dst, ffmpeg_exe=ffmpeg_exe, container=container, overwrite=overwrite):
            ok += 1
        else:
            fail += 1

    log.info(f"Batch complete: {ok} ok, {fail} failed out of {len(files)} files")


def batch_extract_audio(input_dir: Path, output_dir: Path, ffmpeg_exe: str = 'ffmpeg',
                        audio_format: str = 'wav', audio_sample_rate: int = 16000,
                        audio_channels: int = 1, overwrite: bool = False,
                        report_csv: Optional[Path] = None):
    """Extract audio from all .264 files in input_dir."""
    output_dir.mkdir(parents=True, exist_ok=True)
    files = sorted(input_dir.glob("*.264"))
    if not files:
        log.warning(f"No .264 files found in {input_dir}")
        return

    ok, fail = 0, 0
    rows = []
    for src in files:
        dst = output_dir / f"{src.stem}.{audio_format}"
        log.info(f"[{ok+fail+1}/{len(files)}] {src.name} -> {dst.name}")
        success = extract_audio_only(
            input_path=src,
            output_path=dst,
            ffmpeg_exe=ffmpeg_exe,
            audio_format=audio_format,
            audio_sample_rate=audio_sample_rate,
            audio_channels=audio_channels,
            overwrite=overwrite,
        )

        if success:
            ok += 1
            rows.append({
                'source_264': str(src),
                'audio_out': str(dst),
                'status': 'ok',
                'error': '',
                'size_bytes': dst.stat().st_size if dst.exists() else 0,
            })
        else:
            fail += 1
            rows.append({
                'source_264': str(src),
                'audio_out': str(dst),
                'status': 'fail',
                'error': 'conversion_failed',
                'size_bytes': 0,
            })

    if report_csv:
        report_csv.parent.mkdir(parents=True, exist_ok=True)
        with open(report_csv, 'w', newline='', encoding='utf-8') as report_file:
            writer = csv.DictWriter(report_file, fieldnames=['source_264', 'audio_out', 'status', 'error', 'size_bytes'])
            writer.writeheader()
            writer.writerows(rows)
        log.info(f"Audio extraction report: {report_csv}")

    log.info(f"Audio batch complete: {ok} ok, {fail} failed out of {len(files)} files")


def find_ffmpeg():
    """Try to locate ffmpeg; check common WinGet install location."""
    import shutil
    if shutil.which('ffmpeg'):
        return 'ffmpeg'
    # WinGet ffmpeg package path
    winget_base = Path(os.environ.get('LOCALAPPDATA', '')) / 'Microsoft' / 'WinGet' / 'Packages'
    if winget_base.exists():
        matches = list(winget_base.glob("yt-dlp.FFmpeg_*/**/ffmpeg.exe"))
        if matches:
            return str(matches[0])
    return None


def main():
    parser = argparse.ArgumentParser(
        description='Convert HiP2P HXVS .264 files to mp4/avi without Converter.exe')
    parser.add_argument('input', help='Input .264 file or directory (with --batch)')
    parser.add_argument('output', nargs='?', help='Output file or directory (with --batch)')
    parser.add_argument('--batch', action='store_true', help='Batch convert a directory')
    parser.add_argument('--container', choices=['mp4', 'avi'], default='mp4',
                        help='Output container format (default: mp4)')
    parser.add_argument('--audio-only', action='store_true',
                        help='Extract audio only (for ML pipelines)')
    parser.add_argument('--audio-format', choices=['wav', 'flac', 'alaw'], default='wav',
                        help='Audio output format when using --audio-only (default: wav)')
    parser.add_argument('--audio-sample-rate', type=int, default=16000,
                        help='Target audio sample rate for wav/flac (default: 16000)')
    parser.add_argument('--audio-channels', type=int, default=1,
                        help='Target number of audio channels for wav/flac (default: 1)')
    parser.add_argument('--report-csv', default=None,
                        help='Optional CSV report path for batch audio extraction')
    parser.add_argument('--ffmpeg-path', default=None, help='Path to ffmpeg executable')
    parser.add_argument('--overwrite', action='store_true', help='Overwrite existing outputs')
    parser.add_argument('-v', '--verbose', action='store_true', help='Verbose logging')
    args = parser.parse_args()

    if args.verbose:
        log.setLevel(logging.DEBUG)

    ffmpeg_exe = args.ffmpeg_path or find_ffmpeg()
    if not ffmpeg_exe:
        log.error("ffmpeg not found. Install it or use --ffmpeg-path.")
        sys.exit(1)
    log.info(f"Using ffmpeg: {ffmpeg_exe}")

    if args.batch and args.audio_only:
        input_dir = Path(args.input)
        output_dir = Path(args.output) if args.output else input_dir / f"audio_{args.audio_format}"
        report_csv = Path(args.report_csv) if args.report_csv else (output_dir / 'audio_extraction_report.csv')
        batch_extract_audio(
            input_dir=input_dir,
            output_dir=output_dir,
            ffmpeg_exe=ffmpeg_exe,
            audio_format=args.audio_format,
            audio_sample_rate=args.audio_sample_rate,
            audio_channels=args.audio_channels,
            overwrite=args.overwrite,
            report_csv=report_csv,
        )
    elif args.batch:
        input_dir = Path(args.input)
        output_dir = Path(args.output) if args.output else input_dir / 'converted'
        batch_convert(input_dir, output_dir, ffmpeg_exe=ffmpeg_exe,
                      container=args.container, overwrite=args.overwrite)
    else:
        src = Path(args.input)
        if args.output:
            dst = Path(args.output)
        else:
            if args.audio_only:
                dst = src.with_suffix(f".{args.audio_format}")
            else:
                dst = src.with_suffix(f".{args.container}")

        if args.audio_only:
            success = extract_audio_only(
                input_path=src,
                output_path=dst,
                ffmpeg_exe=ffmpeg_exe,
                audio_format=args.audio_format,
                audio_sample_rate=args.audio_sample_rate,
                audio_channels=args.audio_channels,
                overwrite=args.overwrite,
            )
        else:
            success = convert(src, dst, ffmpeg_exe=ffmpeg_exe,
                              container=args.container, overwrite=args.overwrite)
        sys.exit(0 if success else 1)


if __name__ == '__main__':
    main()
