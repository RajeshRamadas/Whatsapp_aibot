"""Bounded local validation of manual audio; no speech service or transcription."""
import json
import subprocess
from pathlib import Path
from tempfile import TemporaryDirectory


class Unavailable(RuntimeError):
    pass


def validate(data, mime):
    format_name = {'audio/aac': 'aac', 'audio/mp4': 'mov', 'audio/wav': 'wav'}.get(mime)
    if not format_name:
        raise ValueError('Choose AAC, audio-only MP4/M4A or WAV audio')
    try:
        with TemporaryDirectory(prefix='meta-audio-') as directory:
            path = Path(directory) / 'input.media'
            path.write_bytes(data)
            # Force a native demuxer, forbid network protocols and external MOV tracks.
            # A private seekable file also handles valid M4A files whose index is at the end.
            options = ['-v', 'error', '-max_alloc', '67108864', '-max_streams', '8',
                       '-protocol_whitelist', 'file,pipe', '-f', format_name]
            if format_name == 'mov':
                options += ['-enable_drefs', '0']
            options += ['-i', str(path)]
            probe = subprocess.run(['ffprobe', *options, '-show_entries', 'stream=codec_type', '-of', 'json'],
                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=5, check=True)
            streams = json.loads(probe.stdout).get('streams', [])
            if not any(row.get('codec_type') == 'audio' for row in streams) or any(
                    row.get('codec_type') == 'video' for row in streams):
                raise ValueError('The file must contain audio without a video track')
            decoded = subprocess.run(['ffmpeg', '-nostdin', '-threads', '1', *options,
                '-map', '0:a:0', '-vn', '-t', '0.1', '-frames:a', '1', '-ac', '1', '-ar', '8000',
                '-f', 's16le', 'pipe:1'],
                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=5, check=True)
            if not decoded.stdout:
                raise ValueError('Audio contains no decodable frames')
    except OSError:
        raise Unavailable('Audio validation is unavailable; contact your administrator') from None
    except (subprocess.SubprocessError, ValueError, TypeError):
        raise ValueError('Choose a valid AAC, audio-only MP4/M4A or WAV recording') from None
