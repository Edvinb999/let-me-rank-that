# Let Me Rank That (@letmerankthat)

Automated Top-5 countdown Shorts built from real, cited data.

- src/topics.py  - data sources + topic registry (all numbers come from here)
- src/script.py  - Claude writes narration; any number not in the data is rejected
- src/tts.py     - ElevenLabs narration, one clip per line
- src/render.py  - 1080x1920 animated leaderboard, captions, source credit
- src/main.py    - builds one Short (`python -m src.main --test`)

Workflows (paste in the browser): .github/workflows/test.yml
Fonts: Anton and Inter, SIL Open Font License.
