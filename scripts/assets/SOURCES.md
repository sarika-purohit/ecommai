# Test assets for `scripts/check_models.py`

## `shirt.jpg`

- **Source:** [File:Shirt images.jpg](https://commons.wikimedia.org/wiki/File:Shirt_images.jpg) on Wikimedia Commons
- **Author:** Mohanraj55 (own work)
- **Licence:** CC0 1.0, public domain dedication (https://creativecommons.org/publicdomain/zero/1.0/)
- **Original:** https://upload.wikimedia.org/wikipedia/commons/9/9b/Shirt_images.jpg, 960 x 1280 px, SHA-256 `4d2e4981e8b30b6db012e4b37fb5c2e4aca69200387ac5ee9fbecfe8e2e64fac`
- **Changes:** resized with Pillow to 750 x 1000 px (long side 1000 px), JPEG quality 85

A folded blue-and-white striped shirt with the collar clearly visible, used for the colour question and the collar box check.

## `hinglish_request.wav`

- **Says:** "Mujhe office ke liye cotton kurta chahiye, pockets ke saath, budget barah sau rupaye."
- **Format:** WAV, 16 kHz, mono, 16-bit PCM, about 5.9 seconds
- **Made with:** [Kokoro-82M](https://huggingface.co/hexgrad/Kokoro-82M) text-to-speech (model weights under Apache-2.0), run through `kokoro-onnx` 0.6.1 with the `kokoro-v1.0.int8.onnx` and `voices-v1.0.bin` files from the [model-files-v1.0 release](https://github.com/thewh1teagle/kokoro-onnx/releases/tag/model-files-v1.0). Hindi male voice `hm_omega`, `lang="hi"`, speed 1.0. The sentence was given in Devanagari so the Hindi voice says the Hindi words naturally:
  `मुझे ऑफिस के लिए कॉटन कुर्ता चाहिए, पॉकेट्स के साथ, बजट बारह सौ रुपये।`
- **Converted** from Kokoro's 24 kHz output on macOS with `afconvert -f WAVE -d LEI16@16000 -c 1 in.wav hinglish_request.wav`
- **Checked:** an independent speech recogniser (Whisper `small`, through `faster-whisper`) heard `मुझे अपिस के लिए कोटन कुर्ता चाहिए पोकेट्स के साथ बजजद बारा सो रुप्ये` and translated it as "I want a cotton kurta for my office, with a budget of Rs. 1200".

The voice is synthetic; no real person was recorded.

To remake it with the macOS Hindi voice Lekha instead (run on a Mac, from the repo root):

```bash
say -v Lekha -o /tmp/hinglish.aiff "मुझे ऑफिस के लिए कॉटन कुर्ता चाहिए, पॉकेट्स के साथ, बजट बारह सौ रुपये।"
afconvert -f WAVE -d LEI16@16000 -c 1 /tmp/hinglish.aiff scripts/assets/hinglish_request.wav
```
