"""Render stable chapter illustrations with semantic outlines and crossfades."""
import argparse
import hashlib
import json
import math
from pathlib import Path
import subprocess
from PIL import Image, ImageDraw, ImageColor


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def probe(path):
    result = subprocess.run(['ffprobe', '-v', 'error', '-show_format', '-show_streams', '-of', 'json', str(path)], capture_output=True, text=True, encoding='utf-8', check=True)
    return json.loads(result.stdout)


def number(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError(f'Invalid numeric value: {value}')
    return value


def load_plan(path):
    path = Path(path).resolve()
    plan = json.loads(path.read_text('utf-8-sig'))
    w, h, fps = [plan.get(k, default) for k, default in [('width',1080),('height',1920),('fps',30)]]
    if any(not isinstance(v, int) or v <= 0 for v in (w,h,fps)) or w % 2 or h % 2 or fps > 60:
        raise ValueError('Invalid canvas/fps')
    safe = plan.get('safeBand', [h * 1480/1920, h * 1770/1920])
    if len(safe) != 2 or not 0 <= number(safe[0]) < number(safe[1]) <= h:
        raise ValueError('Invalid safe band')
    transition = number(plan.get('transition', .35))
    if not 0 < transition <= 1:
        raise ValueError('Transition must be 0..1 second')
    audio = (path.parent / plan['audio']).resolve()
    meta = probe(audio)
    if not any(s['codec_type'] == 'audio' for s in meta['streams']):
        raise ValueError('Audio source has no audio stream')
    duration = float(meta['format']['duration'])
    chapters = plan['chapters']
    if not chapters:
        raise ValueError('No chapters')
    last = 0
    images = set()
    ids = set()
    for chapter in chapters:
        if chapter['id'] in ids:
            raise ValueError('Repeated chapter ID')
        ids.add(chapter['id'])
        start, end = number(chapter['start']), number(chapter['end'])
        if abs(start - last) > .001 or end - start <= transition:
            raise ValueError('Chapters must be contiguous and longer than transition')
        last = end
        image = (path.parent / chapter['image']).resolve()
        image_hash = digest(image)
        if image_hash in images:
            raise ValueError('Each chapter requires a distinct image')
        images.add(image_hash)
        with Image.open(image) as im:
            if abs((im.width/im.height)/(w/h) - 1) > .015:
                raise ValueError('Image aspect differs from canvas; regenerate or extend it')
        chapter['_image'] = str(image)
        chapter['_hash'] = image_hash
        cue_end = start
        for cue in chapter.get('cues', []):
            a, b = number(cue['start']), number(cue['end'])
            if a < start or b > end or b - a < .25 or a < cue_end:
                raise ValueError('Cues must be ordered, nonoverlapping, inside chapter')
            cue_end = b
            if len(cue['box']) != 4:
                raise ValueError('box must be x,y,width,height')
            x, y, bw, bh = map(number, cue['box'])
            if min(x,y) < 0 or min(bw,bh) <= 0 or x+bw > w or y+bh > h:
                raise ValueError('Cue box outside canvas')
            if y < safe[1] and y+bh > safe[0]:
                raise ValueError('Cue intersects future subtitle safe band')
            ImageColor.getrgb(cue.get('color', '#DC9648'))
    if abs(last - duration) > .1:
        raise ValueError(f'Chapter end {last} does not match audio duration {duration}')
    return plan, audio, duration, w, h, fps, transition


def render(path, output):
    plan, audio, duration, w, h, fps, transition = load_plan(path)
    output = Path(output).resolve()
    work = output.parent / (output.stem + '.render')
    record = output.with_suffix('.render.json')
    if output.exists() or work.exists() or record.exists():
        raise ValueError('Output/intermediates exist; choose a new output filename')
    work.mkdir(parents=True)
    calls = []

    def run(args):
        calls.append(args)
        record.write_text(json.dumps({'status': 'rendering', 'commands': calls}, ensure_ascii=False, indent=2), 'utf-8')
        subprocess.run(args, check=True)

    clips = []
    chapters = plan['chapters']
    for index, chapter in enumerate(chapters):
        lead = transition / 2 if index else 0
        tail = transition / 2 if index < len(chapters)-1 else 0
        length = chapter['end'] - chapter['start'] + lead + tail
        args = ['ffmpeg','-hide_banner','-loglevel','error','-nostdin','-n','-filter_complex_threads','1',
                '-loop','1','-framerate',str(fps),'-i',chapter['_image']]
        graph = [f'[0:v]scale={w}:{h}:force_original_aspect_ratio=decrease,pad={w}:{h}:(ow-iw)/2:(oh-ih)/2:color=0xF5E9D8,setsar=1,format=yuv420p[b0]']
        for j, cue in enumerate(chapter.get('cues', []), 1):
            overlay = Image.new('RGBA', (w,h), (0,0,0,0))
            draw = ImageDraw.Draw(overlay)
            x,y,bw,bh = cue['box']
            draw.rounded_rectangle((x,y,x+bw-1,y+bh-1), radius=max(4,round(w*.02)), outline=ImageColor.getrgb(cue.get('color','#DC9648'))+(235,), width=max(3,round(w*.006)))
            png = work / f'chapter-{index:03d}-cue-{j:03d}.png'
            overlay.save(png)
            args.extend(['-loop','1','-framerate',str(fps),'-i',str(png)])
            a = cue['start'] - chapter['start'] + lead
            b = cue['end'] - chapter['start'] + lead
            fade = min(.2,(b-a)/3)
            outfade = min(.25,(b-a)/3)
            graph.append(f'[{j}:v]format=rgba,fade=t=in:st={a:.6f}:d={fade:.6f}:alpha=1,fade=t=out:st={b-outfade:.6f}:d={outfade:.6f}:alpha=1[o{j}]')
            graph.append(f'[b{j-1}][o{j}]overlay=0:0:format=auto[b{j}]')
        clip = work / f'chapter-{index:03d}.mp4'
        last = len(chapter.get('cues', []))
        args.extend(['-filter_complex',';'.join(graph),'-map',f'[b{last}]','-an','-t',f'{length:.6f}',
                     '-r',str(fps),'-c:v','libx264','-threads','2','-preset','veryfast','-crf','18','-pix_fmt','yuv420p',str(clip)])
        run(args)
        clips.append(clip)
    args = ['ffmpeg','-hide_banner','-loglevel','error','-nostdin','-n','-filter_complex_threads','1']
    for clip in clips:
        args.extend(['-i',str(clip)])
    args.extend(['-i',str(audio)])
    graph = [f'[{i}:v]settb=AVTB,setpts=PTS-STARTPTS[v{i}]' for i in range(len(clips))]
    prev = 'v0'
    for i in range(1,len(clips)):
        offset = chapters[i]['start'] - transition/2
        graph.append(f'[{prev}][v{i}]xfade=transition=fade:duration={transition:.6f}:offset={offset:.6f}[x{i}]')
        prev = f'x{i}'
    graph.append(f'[{prev}]tpad=stop_mode=clone:stop_duration=0.1,trim=duration={duration:.6f},format=yuv420p[outv]')
    args.extend(['-filter_complex',';'.join(graph),'-map','[outv]','-map',f'{len(clips)}:a:0',
                 '-sn','-dn','-t',f'{duration:.6f}','-r',str(fps),'-c:v','libx264','-threads','2',
                 '-preset','medium','-crf','18','-pix_fmt','yuv420p','-c:a','aac','-ar','48000',
                 '-b:a','192k','-movflags','+faststart',str(output)])
    run(args)
    record.write_text(json.dumps({'status':'rendered-technical-review-required','planSha256':digest(path),
                                 'videoSha256':digest(output),'audioSha256':digest(audio),
                                 'captionRendering':False,'imageSha256':[c['_hash'] for c in chapters],
                                 'commands':calls}, ensure_ascii=False, indent=2), 'utf-8')
    print(str(output))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('plan',type=Path)
    parser.add_argument('--out',type=Path,required=True)
    args = parser.parse_args()
    render(args.plan,args.out)
