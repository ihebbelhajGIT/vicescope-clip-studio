# Third-party notice

The upstream project https://github.com/Bodila51/gtavi-claudecode was consulted for architectural ideas. No upstream source code was incorporated. It is distributed under its own MIT license, which does not grant rights to third-party footage. Dependencies are distributed under their respective licenses; consult their packages for current license details.

The faster-whisper dependency includes the MIT-licensed Silero VAD model (copyright © 2020-present Silero Team). The package includes the dependency metadata. The upstream license is available at https://github.com/snakers4/silero-vad/blob/master/LICENSE.

The app uses external FFmpeg and FFprobe executables for rendering. Native builds may contain FFmpeg shared libraries from PyAV and Qt Multimedia. See the [packaging guide](docs/build-and-package.md) for redistribution considerations.

## Silero VAD license

```text
MIT License

Copyright (c) 2020-present Silero Team

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```
