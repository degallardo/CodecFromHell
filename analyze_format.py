import struct, sys, os

def analyze(filepath):
    with open(filepath, 'rb') as f:
        data = f.read()

    magic = data[0:4]
    print(f"File: {os.path.basename(filepath)}")
    print(f"File magic: {magic}")
    w = struct.unpack_from('<H', data, 4)[0]
    h = struct.unpack_from('<H', data, 8)[0]
    print(f"Resolution from HXVS header: {w}x{h}")
    print(f"HXVS header bytes: {data[0:16].hex()}")

    offset = 16
    frame_count = 0
    flag_counts = {}
    timestamps = []
    payload_sizes = []

    nal_names = {1:'non-IDR', 5:'IDR', 7:'SPS', 8:'PPS', 9:'AUD', 0:'Unspec'}

    while offset + 16 <= len(data):
        fmagic = data[offset:offset+4]
        if fmagic != b'HXVF':
            print(f"  WARN: unexpected magic at offset {hex(offset)}: {fmagic.hex()}")
            # Try to find next HXVF
            nxt = data.find(b'HXVF', offset+1)
            if nxt == -1:
                break
            print(f"  Skipping {nxt-offset} bytes to next HXVF at {hex(nxt)}")
            offset = nxt
            continue

        payload_size = struct.unpack_from('<I', data, offset+4)[0]
        ts           = struct.unpack_from('<I', data, offset+8)[0]
        flags        = struct.unpack_from('<I', data, offset+12)[0]
        flag_counts[flags] = flag_counts.get(flags, 0) + 1
        timestamps.append(ts)
        payload_sizes.append(payload_size)

        if frame_count < 8:
            sc = data[offset+16:offset+20]
            nal_byte = data[offset+20] if offset+20 < len(data) else 0
            nal_type = nal_byte & 0x1F
            print(
                f"  Frame {frame_count:4d}: off={hex(offset):8s}  "
                f"size={payload_size:8d}  ts={ts:12d}  "
                f"flags={flags}  sc={sc.hex()}  "
                f"NAL_type={nal_type}({nal_names.get(nal_type,'?')})"
            )

        offset += 16 + payload_size
        frame_count += 1

    print(f"\nTotal frames parsed: {frame_count}")
    print(f"Flag distribution:   {flag_counts}")

    if len(timestamps) > 1:
        diffs = [timestamps[i+1]-timestamps[i] for i in range(min(30, len(timestamps)-1)) if timestamps[i+1] != timestamps[i]]
        print(f"Timestamp diffs sample (non-zero): {diffs[:20]}")
        print(f"TS range: {timestamps[0]} -> {timestamps[-1]}  delta={timestamps[-1]-timestamps[0]}")
        if diffs:
            import statistics
            avg = statistics.mean(diffs)
            print(f"Avg TS diff: {avg:.1f}  (if ms => fps~={1000/avg:.2f})")

    print(f"Payload size range: {min(payload_sizes)} - {max(payload_sizes)}")
    print()

analyze(r'C:\Users\sesa443933\Videos\Borrar\P241223_142018_143018.264')
analyze(r'C:\Users\sesa443933\Videos\Borrar\P241223_143018_144017.264')
