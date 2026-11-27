"""Bounded local validation of manual audio/MP4 video; no AI interpretation."""
import json
import subprocess
from pathlib import Path
from tempfile import TemporaryDirectory


class Unavailable(RuntimeError):
    pass


def validate(data, mime):
    format_name = {'audio/aac': 'aac', 'audio/mp4': 'mov', 'audio/wav': 'wav', 'video/mp4': 'mov'}.get(mime)
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
            probe = subprocess.run(['ffprobe', *options, '-show_entries',
                'stream=codec_type,codec_name,width,height', '-of', 'json'],
                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=5, check=True)
            streams = json.loads(probe.stdout).get('streams', [])
            videos = [row for row in streams if row.get('codec_type') == 'video']
            audios = [row for row in streams if row.get('codec_type') == 'audio']
            if videos:
                if (mime != 'video/mp4' or len(videos) != 1 or videos[0].get('codec_name') != 'h264'
                        or not 0 < videos[0].get('width', 0) <= 1920
                        or not 0 < videos[0].get('height', 0) <= 1920
                        or len(audios) > 1 or any(row.get('codec_name') != 'aac' for row in audios)):
                    raise ValueError('Unsupported video tracks')
            if not videos and not audios:
                raise ValueError('No playable track')
            outputs = []
            if videos:
                outputs.append(['-map', '0:v:0', '-an', '-frames:v', '1', '-vf', 'scale=16:16',
                                '-threads', '1', '-pix_fmt', 'rgb24', '-f', 'rawvideo', 'pipe:1'])
            if audios:
                outputs.append(['-map', '0:a:0', '-vn', '-t', '0.1', '-frames:a', '1', '-ac', '1', '-ar', '8000',
                                '-f', 's16le', 'pipe:1'])
            # Bound video decoder memory/CPU independently of its small output frame.
            # Allow shared-library mappings on newer distro builds while bounding decoding.
            limits = ['prlimit', '--as=1073741824', '--cpu=4', '--'] if videos else []
            for output in outputs:
                try:
                    decoded = subprocess.run([*limits, 'ffmpeg', '-nostdin', '-threads', '1', '-filter_threads', '1',
                        *options, *output], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=5, check=True)
                except subprocess.CalledProcessError as exc:
                    if videos and exc.returncode in (126, 127):
                        raise OSError('Decoder could not start') from None
                    raise
                if not decoded.stdout:
                    raise ValueError('No decodable frame')
            return 'video' if videos else 'audio'
    except OSError:
        raise Unavailable('Media validation is unavailable; contact your administrator') from None
    except (subprocess.SubprocessError, ValueError, TypeError):
        raise ValueError('Choose valid AAC/M4A/WAV audio or H.264 MP4 video '
                         'up to 1920×1920 with optional AAC audio') from None
