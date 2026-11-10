"""Evaluate a configured speech adapter on a private JSON manifest; never log transcripts."""
import argparse
import json
import unicodedata
from collections import defaultdict
from pathlib import Path

from app.config import settings
from app.services.media_intelligence import audio, transcribe

LANGUAGES = {'en', 'hi', 'kn', 'ta', 'te', 'mr', 'bn', 'hi-en'}


def errors(reference, actual):
    def words(value):
        value = unicodedata.normalize('NFKC', value.casefold())
        return ''.join(' ' if unicodedata.category(char)[0] in 'PS' else char for char in value).split()
    expected, observed = words(reference), words(actual)
    previous = list(range(len(observed) + 1))
    for i, word in enumerate(expected, 1):
        current = [i]
        for j, other in enumerate(observed, 1):
            current.append(min(current[-1] + 1, previous[j] + 1, previous[j - 1] + (word != other)))
        previous = current
    return previous[-1], len(expected)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('manifest', type=Path)
    args = parser.parse_args()
    if settings.transcription_provider != 'openai':
        parser.error('Set TRANSCRIPTION_PROVIDER=openai for this evaluation')
    scores = defaultdict(lambda: [0, 0, 0])
    for sample in json.loads(args.manifest.read_text()):
        if sample['language'] not in LANGUAGES or not isinstance(sample['reference'], str) or not sample['reference']:
            parser.error('Samples need a supported language and a nonempty reference transcript')
        path = args.manifest.parent / sample['file']
        with path.open('rb') as source:
            data = source.read(settings.media_max_bytes + 1)
        if len(data) > settings.media_max_bytes:
            parser.error('A sample exceeds MEDIA_MAX_BYTES')
        wav, _ = audio(data)
        text, _ = transcribe(wav)
        wrong, total = errors(sample['reference'], text)
        score = scores[sample['language']]
        score[0] += wrong
        score[1] += total
        score[2] += 1
    print(json.dumps({'provider': 'openai', 'model': settings.transcription_model,
        'missing_languages': sorted(LANGUAGES - scores.keys()),
        'results': {language: {'samples': count, 'word_error_rate': wrong / max(total, 1)}
                    for language, (wrong, total, count) in scores.items()}}, indent=2))


if __name__ == '__main__':
    try:
        main()
    except Exception:
        raise SystemExit('Speech evaluation failed; check the manifest, decoder and provider configuration.') from None
