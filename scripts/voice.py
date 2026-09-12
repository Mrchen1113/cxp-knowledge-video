"""Chapter TTS or local ASR; never adds captions or uploads reference audio."""
import argparse
import asyncio
import hashlib
import json
from pathlib import Path
import re
import subprocess
import wave


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


async def tts(args):
    import edge_tts
    profile = json.loads((Path(__file__).parent.parent / 'assets/voice-profile.json').read_text('utf-8'))
    data = json.loads(args.input.read_text('utf-8-sig'))
    chapters = data['chapters']
    ids = set()
    if not chapters:
        raise ValueError('No chapters')
    for chapter in chapters:
        ident = chapter['id']
        if not re.fullmatch(r'[A-Za-z0-9_-]+', ident) or ident in ids:
            raise ValueError('Chapter IDs must be distinct safe filenames')
        ids.add(ident)
        if not isinstance(chapter['text'], str) or not chapter['text'].strip():
            raise ValueError('Empty chapter text')
    args.out.mkdir(parents=True, exist_ok=True)
    if any(args.out.iterdir()):
        raise ValueError('Use an empty audio output directory; existing work is preserved')
    voice = args.voice or profile['voice']
    rate = args.rate or profile['rate']
    names = {v['ShortName'] for v in await edge_tts.list_voices()}
    if voice not in names:
        raise ValueError(f'Voice currently unavailable: {voice}')
    rows = []
    full = args.out / 'narration.wav'
    cursor_frames = 0
    with wave.open(str(full), 'wb') as writer:
        writer.setnchannels(1)
        writer.setsampwidth(2)
        writer.setframerate(48000)
        for index, chapter in enumerate(chapters):
            mp3 = args.out / (chapter['id'] + '.mp3')
            wav = args.out / (chapter['id'] + '.wav')
            boundaries = []
            communication = edge_tts.Communicate(chapter['text'], voice, rate=rate, pitch=profile['pitch'], boundary='WordBoundary')
            with mp3.open('wb') as target:
                async for event in communication.stream():
                    if event['type'] == 'audio':
                        target.write(event['data'])
                    elif event['type'] in ('WordBoundary', 'SentenceBoundary'):
                        item = {k: v for k, v in event.items() if k != 'data'}
                        item['absoluteStartSeconds'] = cursor_frames / 48000 + event['offset'] / 10000000
                        item['absoluteEndSeconds'] = item['absoluteStartSeconds'] + event['duration'] / 10000000
                        boundaries.append(item)
            subprocess.run(['ffmpeg', '-v', 'error', '-nostdin', '-n', '-i', str(mp3), '-vn', '-ac', '1', '-ar', '48000', '-c:a', 'pcm_s16le', str(wav)], check=True)
            start = cursor_frames / 48000
            with wave.open(str(wav), 'rb') as reader:
                count = reader.getnframes()
                if count < 4800:
                    raise ValueError('Generated audio is too short')
                writer.writeframes(reader.readframes(count))
            cursor_frames += count
            speech_end = cursor_frames / 48000
            if index < len(chapters) - 1:
                pause = round(args.pause * 48000)
                writer.writeframes(b'\0\0' * pause)
                cursor_frames += pause
            rows.append({'id': chapter['id'], 'text': chapter['text'], 'start': start,
                         'speechEnd': speech_end, 'end': cursor_frames / 48000,
                         'audio': wav.name, 'sha256': sha(wav), 'providerBoundaries': boundaries})
    han = sum(len(re.findall(r'[\u4e00-\u9fff]', x['text'])) for x in chapters)
    report = {'provider': profile['provider'], 'voice': voice, 'rate': rate,
              'referenceAudioUploaded': False, 'captionsGenerated': False,
              'audio': full.name, 'sha256': sha(full), 'duration': cursor_frames / 48000,
              'chineseCharactersPerMinute': round(han / (cursor_frames / 48000) * 60, 1),
              'chapters': rows}
    (args.out / 'timing.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), 'utf-8')
    print(json.dumps({k: v for k, v in report.items() if k != 'chapters'}, ensure_ascii=False))


def transcribe(args):
    from faster_whisper import WhisperModel
    if args.out.exists():
        raise ValueError('Output already exists')
    model = WhisperModel(args.model, device='cpu', compute_type='int8', local_files_only=True)
    segments, info = model.transcribe(str(args.input), language='zh', beam_size=3, word_timestamps=True, vad_filter=True)
    rows = [{'start': s.start, 'end': s.end, 'text': s.text,
             'words': [{'start': w.start, 'end': w.end, 'text': w.word} for w in (s.words or [])]}
            for s in segments]
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps({'status': 'uncorrected-asr', 'source': str(args.input.resolve()),
                                    'sha256': sha(args.input), 'duration': info.duration,
                                    'segments': rows}, ensure_ascii=False, indent=2), 'utf-8')
    print(str(args.out))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_subparsers(dest='mode', required=True)
    speech = modes.add_parser('tts')
    speech.add_argument('input', type=Path)
    speech.add_argument('--out', type=Path, required=True)
    speech.add_argument('--voice')
    speech.add_argument('--rate')
    speech.add_argument('--pause', type=float, default=.28)
    asr = modes.add_parser('transcribe')
    asr.add_argument('input', type=Path)
    asr.add_argument('--out', type=Path, required=True)
    asr.add_argument('--model', default='small')
    options = parser.parse_args()
    if options.mode == 'tts':
        if not 0 <= options.pause <= 2:
            parser.error('pause must be 0..2 seconds')
        asyncio.run(tts(options))
    else:
        transcribe(options)
