# Let Me Rank That (@letmerankthat)

Automated Top-5 countdown Shorts built from real, cited data.

- src/topics.py  - data sources + topic registry (all numbers come from here)
- src/script.py  - Claude writes narration; any number not in the data is rejected
- src/tts.py     - ElevenLabs narration, one clip per line
- src/render.py  - 1080x1920 animated leaderboard, captions, source credit
- src/main.py    - builds one Short (`python -m src.main --test`)

Workflows (paste in the browser):
- test.yml    - manual test render, no upload
- shorts.yml  - publishes a Short at 15:00 and 21:00 UTC (auto topic + voice)
- manage.yml  - Mondays 07:30 UTC: analytics -> state/weights.json -> report issue

State: state/history.json (uploads), state/weights.json (learned weights).
Add a topic: add it to data/facts.yaml with a source for every number.
Fonts: Anton and Inter, SIL Open Font License.
