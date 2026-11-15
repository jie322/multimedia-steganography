# 本机测试资源

`downloads/test.jpg` 和 `downloads/*.mp4` 是项目测试的原始文件。原始 MP4 含 48 kHz 双声道 AAC 音轨，不需要另外准备音乐。运行 `test/prepare_resources.py` 会用 FFmpeg 从该视频生成测试音频、短视频和载荷。

## 由原始文件生成的主要资源

| 文件 | 用途 |
| --- | --- |
| `carriers/image_carrier.png`、`image_carrier.bmp` | 由 JPG 转换，验证其他图片输入格式 |
| `carriers/audio_full.wav` | 从完整 MP4 音轨提取的 48 kHz、双声道、16-bit WAV |
| `carriers/audio_20s.wav` | 音轨前 20 秒，用于常规 WAV 隐写测试 |
| `carriers/audio_8bit.wav` 至 `audio_32bit.wav` | 验证不同 PCM 位深的低位写入和恢复 |
| `payloads/payload_music.mp3` | 音轨前 5 秒，作为隐藏文件测试 |
| `payloads/payload_video_5s.mp4` | 原视频前 5 秒压缩版，作为隐藏文件测试 |
| `payloads/payload_image.jpg` | 原始 JPG 副本，作为隐藏文件测试 |
| `payloads/secret_text_utf8.txt` | 中英日混合文本测试 |

## 测试方法

在项目目录的虚拟环境中执行：

```powershell
.venv\Scripts\python.exe test\prepare_resources.py
.venv\Scripts\python.exe test\run_project_tests.py
```

测试脚本通过本地 HTTP 接口验证图片、WAV、文本、图片文件、音频文件、短视频文件的嵌入与逐字节恢复，并检查容量限制、错误输入、低位可视化和 PCM 采样值差异。详细结果保存在 `output/testing/test_results.json`。

`test.jpg` 在 2 位 LSB 下可用容量约 1.9MB；20 秒 WAV 约 480KB。完整 MP4 约 23MB，超过两者容量，会被拒绝；压缩后的 5 秒 MP4 约 130KB，可作为待隐藏文件。当前项目支持图片和 WAV 作为载体，MP4 文件仅作为隐藏内容使用。
