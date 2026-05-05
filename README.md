# CodecFromHell

Convert proprietary HXVS `.264` files (HiP2P / HiSilicon style) into standard media formats using Python and ffmpeg.

This project was built to solve a practical problem: many HXVS `.264` files cannot be converted correctly with generic tools out of the box.

## What This Repository Does

- Parses the proprietary HXVS container structure.
- Extracts video and audio payloads from frame blocks.
- Computes realistic FPS from embedded timestamps.
- Muxes to `.mp4` or `.avi` via ffmpeg.
- Can extract audio-only outputs (`wav`, `flac`, or raw `alaw`).
- Includes optional benchmark and distributed automation scripts.

## Main Script

- `hxvs_converter.py`: main converter.

## How It Works (High Level)

The converter reads a `.264` file as HXVS structured data:

1. File header (`HXVS`) provides metadata including width and height.
2. Frame blocks are parsed sequentially:
   - `HXVF`: video frame payload
   - `HXAF`: audio chunk payload (G.711 A-law with a small internal sub-header)
   - `HXFI` / `HXFC`: informational/config blocks (ignored)
3. Video payloads are written as raw Annex B H.264.
4. Audio payloads are written as raw A-law.
5. ffmpeg is invoked to mux streams into the selected output container.

FPS is derived from timestamps and encoded frame count, excluding non-display NAL units like SPS/PPS/AUD when estimating effective frame rate.

## Requirements

- Python 3.10+
- ffmpeg available in `PATH` (or pass `--ffmpeg-path`)

Install dependencies:

```bash
pip install -r requirements.txt
```

## Usage

### Single File Conversion

```bash
python hxvs_converter.py input.264 output.mp4
```

If `output` is omitted, extension is inferred from `--container` (default `mp4`).

### Batch Conversion

```bash
python hxvs_converter.py --batch <input_dir> <output_dir>
```

### Choose Container

```bash
python hxvs_converter.py input.264 output.avi --container avi
```

### Extract Audio Only

```bash
python hxvs_converter.py input.264 --audio-only --audio-format wav
```

Optional audio settings:

- `--audio-sample-rate` (default `16000`)
- `--audio-channels` (default `1`)

### Common Flags

- `--ffmpeg-path <path>`: explicit ffmpeg executable
- `--overwrite`: overwrite existing outputs
- `-v` / `--verbose`: debug logging

## Why MP4 Audio Is Transcoded

MP4 does not natively support `pcm_alaw` in a broadly compatible way. For MP4 outputs, audio is transcoded to AAC. For AVI outputs, A-law can be copied directly.

## Helper Scripts

- `analyze_format.py`: inspect HXVS frame structure and timing from one or more files.
- `benchmark_ffmpeg_cpu_nvenc.py`: benchmark CPU (`libx264`) vs NVIDIA NVENC (`h264_nvenc`) re-encoding speed.

## Automation Folder (Optional)

The `automation/` folder contains queue/table based distributed processing utilities for Azure Storage:

- enqueue jobs from CSV manifests
- process jobs with a worker
- export status reports
- mark verified jobs

Use this only if you need batch/distributed orchestration.

## Safety Notes

- Never commit real secrets (connection strings, API keys, private manifests).
- Use `automation/config.example.json` as a template and keep real config local (`automation/config.json` is gitignored).

## Disclaimer

- This software is provided "as is", without warranties of any kind.
- Use at your own risk.
- Always keep backups of original source files before running conversions or batch jobs.
- The authors and contributors are not liable for data loss, corruption, or other damages resulting from use of this project.

## Known Limitations

- This implementation targets the observed HXVS variant used in tested files. Other `.264` vendor formats may differ.
- If ffmpeg cannot parse your resulting bitstream, share a sample and logs to improve parser compatibility.

## Contributing

Bug reports and sample-driven fixes are welcome.

If possible, include:

- a short sample file,
- command used,
- converter log output,
- ffmpeg version (`ffmpeg -version`).

## License

This project is licensed under the MIT License. See [LICENSE](LICENSE).

## AI Assistance Notice

Parts of this repository (including code and documentation) were created with AI assistance and then reviewed and validated by a human before publication.