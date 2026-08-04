# Sources and Assumptions

Checked during preparation on 4 August 2026:

- Open Acoustic Devices describes AudioMoth USB Microphone as a Raspberry Pi/Linux-compatible full-spectrum USB microphone supporting sample rates up to 384 kHz.
- BirdNET-Analyzer and the BirdNET Python library publish MIT-licensed source code, with distributed model assets under separate CC BY-NC-SA 4.0 terms. Installation and redistribution must preserve that distinction.
- BatDetect2 describes itself as a deep-learning model/software package for detecting and classifying bat echolocation calls in high-frequency recordings.
- BirdNET-Go is a relevant implementation reference for continuous Raspberry Pi soundscape analysis and recent multi-model operation, but Open Observatory remains independently specified.

## Assumptions requiring target-device verification

- Exact AudioMoth USB firmware and switch configuration.
- ALSA-visible sample rates, formats and channel count.
- Sustained Pi 5 capture stability at 384 kHz.
- Whether USB/host power and enclosure produce electrical or fan noise.
- BirdNET and BatDetect2 ARM64 dependency compatibility.
- BatDetect2 real-time throughput and useful UK taxonomy on this hardware.
- Required external storage capacity and retention preferences.

## Licensing warning

This seed is not legal advice. Claude Code must add a third-party notices workflow and must not bundle model files until their exact licence and distribution conditions are reviewed.
