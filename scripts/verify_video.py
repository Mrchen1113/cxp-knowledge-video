"""Narrow technical QC and review frames; cannot certify visual/semantic quality."""
import argparse
import json
from pathlib import Path
import re
import subprocess
from render_video import load_plan, probe, digest


def verify(plan_path, video, output):
    plan, audio, expected, w, h, fps, transition = load_plan(plan_path)
    meta = probe(video)
    errors = []
    streams = meta['streams']
    vs = [s for s in streams if s['codec_type']=='video']
    aus = [s for s in streams if s['codec_type']=='audio']
    if len(vs)!=1 or len(aus)!=1 or len(streams)!=2:
        errors.append('Expected exactly video+audio, no subtitle/data streams')
    if vs and (vs[0]['width']!=w or vs[0]['height']!=h):
        errors.append('Wrong dimensions')
    if vs:
        num,den = map(float,vs[0]['avg_frame_rate'].split('/'))
        if abs(num/den-fps)>.02:
            errors.append('Wrong fps')
    if abs(float(meta['format']['duration'])-expected)>.15:
        errors.append('Duration does not match original audio')
    if vs and aus and abs(float(vs[0].get('duration',expected))-float(aus[0].get('duration',expected)))>.15:
        errors.append('Audio/video duration mismatch')
    result = subprocess.run(['ffmpeg','-hide_banner','-nostdin','-i',str(video),'-vf','blackdetect=d=0.08:pix_th=0.02:pic_th=0.98','-af','volumedetect','-f','null','-'], capture_output=True, text=True, encoding='utf-8', errors='replace')
    if result.returncode:
        errors.append('Decode failed')
    volume = re.search(r'mean_volume:\s*(-?[\d.]+) dB',result.stderr)
    mean = float(volume.group(1)) if volume else None
    if mean is None or mean < -45:
        errors.append('Missing or very quiet audio')
    black = re.findall(r'black_start:([\d.]+) black_end:([\d.]+) black_duration:([\d.]+)',result.stderr)
    if black:
        errors.append('Black interval detected; inspect transitions')
    record_path = Path(video).with_suffix('.render.json')
    if record_path.exists():
        record=json.loads(record_path.read_text('utf-8'))
        if record.get('planSha256')!=digest(plan_path) or record.get('videoSha256')!=digest(video) or record.get('audioSha256')!=digest(audio):
            errors.append('Render evidence hash does not match inputs/output')
    else:
        errors.append('Missing render evidence')
    frames=output.parent/(output.stem+'-frames')
    frames.mkdir(parents=True,exist_ok=True)
    times={0,max(0,expected-.1)}
    for chapter in plan['chapters']:
        times.add((chapter['start']+chapter['end'])/2)
        if chapter['start']:
            times.update([max(0,chapter['start']-transition),chapter['start'],chapter['start']+transition])
    screenshots=[]
    for i,t in enumerate(sorted(times)):
        dest=frames/f'{i:03d}-{t:.3f}s.jpg'
        subprocess.run(['ffmpeg','-v','error','-nostdin','-y','-ss',str(t),'-i',str(video),'-frames:v','1','-q:v','2',str(dest)],check=True)
        screenshots.append(str(dest.resolve()))
    report={'technicalPass':not errors,'errors':errors,'duration':float(meta['format']['duration']),
            'chapterCount':len(plan['chapters']),'meanVolumeDb':mean,'blackIntervals':black,
            'captionStreamPresent':any(s['codec_type']=='subtitle' for s in streams),
            'visualReviewStatus':'required','voiceSimilarityStatus':'requires-perceptual-review',
            'note':'No subtitle stream does not prove source images contain no burned-in captions.',
            'screenshots':screenshots,'videoSha256':digest(video)}
    output.parent.mkdir(parents=True,exist_ok=True)
    output.write_text(json.dumps(report,ensure_ascii=False,indent=2),'utf-8')
    print(json.dumps({k:v for k,v in report.items() if k!='screenshots'},ensure_ascii=False))
    return not errors


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('plan',type=Path)
    parser.add_argument('video',type=Path)
    parser.add_argument('--out',type=Path,required=True)
    args=parser.parse_args()
    raise SystemExit(0 if verify(args.plan,args.video,args.out) else 1)
