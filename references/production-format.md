# 脚本与时间线

路径相对JSON文件目录。脚本用参数列表调用FFmpeg，不构造shell命令。依赖Python/Pillow、FFmpeg/ffprobe。

## 配音输入

```json
{"chapters": [{"id": "01", "text": "先说结论，再给理由，最后提出方案。"}, {"id": "02", "text": "先让对方知道重点，再解释你的判断。"}]}
```

`python scripts/voice.py tts narration-chapters.json --out audio`，音色速率默认读voice-profile，可用`--voice`、`--rate=+18%`覆盖。章节停顿计入前章，真实音频与timing.json一致。

`python scripts/voice.py transcribe input.wav --out words.json --model small`，仅输出带时间码草稿，无SRT。

## 视频输入

```json
{
  "title": "表达顺序", "inputMode": "article",
  "audio": "audio/narration.wav",
  "width": 1080, "height": 1920, "fps": 30,
  "transition": 0.35, "safeBand": [1480, 1770],
  "chapters": [{
    "id": "01", "image": "images/01.png", "start": 0, "end": 6,
    "cues": [
      {"start": 0.7, "end": 2.5, "keyword": "结论", "box": [80,520,410,360], "color": "#88964F"},
      {"start": 3, "end": 5.6, "keyword": "理由", "box": [570,520,410,360], "color": "#6088A7"}
    ]
  }]
}
```

此处6秒是字段示意，必须改成实际时长。所有start/end/cues是**全片绝对秒数**。章节连续，最后end与音频相差不超过0.1s。box是最终画布像素`[x,y,width,height]`；cues不重叠、在本章内部，不进入safeBand。一章一图，不同章图片文件及内容哈希不能相同。keyword用于质检，不绘制成字幕。

`python scripts/render_video.py production.json --out final.mp4`：静图底、圆角描边渐显、短叠化，生成final.render目录与final.render.json记录，不删除文件；已有输出报错，重跑用新输出名。

`python scripts/verify_video.py production.json final.mp4 --out quality-report.json`：技术报告及首尾/各章/强调/转场抽帧。渲染器无字幕通路，但没有字幕流不代表图像里没字幕；必须看抽帧。另写review.json，用具体章号、截图和观察记录风格、可读性、无口播字幕、留白、同步，没实际看/听不能填写通过。
