# LocalForge AI - Phase 7 prompt (approved)

```
PHASE 7: Voice, files, images and videos - free and local

- Voice input: Whisper on this PC (no audio leaves the computer); spoken answers
  with the Windows voices in the browser.
- Create files: a File agent writes one safe file into projects\files.
- Images: SD-Turbo with stable-diffusion.cpp on the CPU (512x512).
- Videos: storyboard -> one picture per scene -> narration -> slideshow MP4
  (FFmpeg). Realistic AI motion video is out of scope: it needs a 12-24 GB GPU.
- Every download shows name, source and exact size and waits for "yes".

ACCEPTANCE TESTS:
1. All automated tests pass.
2. Voice input, images, video and narration are installed.
3. Speech is understood on this PC.
4. Edge shows the microphone, Speak answers and the Create tab.
5. A file is created, saved, valid and downloadable.
6. An image is generated on this PC.
7. A narrated slideshow video is made.
8. A creation can be cancelled within 10 s.
9. Other websites cannot create things; unknown downloads refused.
10. Results saved to D:\LocalForgeAI\docs\phase7-report.md
```
