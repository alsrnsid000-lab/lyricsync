# lyricsync

음원에서 가사 타임스탬프를 자동으로 뽑는 프로그램. 윈도우 exe로 묶어 공유할 수 있다.
LRC / SRT / VTT / JSON / TSV 출력.

두 가지 모드로 돌아간다.

| | 입력 | 출력 글자 | 용도 |
|---|---|---|---|
| **가사 있음** | 음원 + 가사 텍스트 | 가사 파일 그대로 (오타 0) | 최종본 |
| **가사 없음** | 음원만 | ASR 받아쓰기 (오류 섞임) | 초벌 |

핵심은 **글자와 시간을 분리**하는 것이다. 가사를 주면 ASR 결과에서 '시간'만 빌려오고 글자는 네 가사를 그대로 쓴다. ASR이 "있었어"를 "이써써"로 들어도 출력에는 "있었어"가 박힌다.

---

## 윈도우용으로 빌드해서 공유하기

**exe는 윈도우에서만 만들 수 있다.** PyInstaller는 크로스 컴파일을 지원하지 않는다. 이 저장소는 GitHub Actions의 윈도우 러너에서 자동 빌드하도록 설정해 뒀으니, 윈도우 PC가 없어도 된다.

### 1. 저장소에 올린다

```bash
git init
git add .
git commit -m "lyricsync 초기 커밋"
git remote add origin https://github.com/<네아이디>/lyricsync.git
git push -u origin main
```

### 2. 빌드를 돌린다

**방법 A — 릴리스까지 자동으로** (공유용으로 추천)

```bash
git tag v0.1.0
git push --tags
```

태그를 밀면 빌드가 돌고, 끝나면 **Releases 페이지에 zip이 올라간다.** 그 링크를 사람들에게 주면 된다.

**방법 B — 시험 빌드만**

GitHub 저장소 → `Actions` 탭 → `Build Windows` → `Run workflow` 버튼. 끝나면 그 실행 페이지 아래 `Artifacts`에서 zip을 받을 수 있다 (30일 보관).

빌드는 **40~70분** 걸린다. torch 설치와 PyInstaller 분석이 느리다. 무료 계정도 공개 저장소면 무료다.

### 3. 받는 사람에게 전달한다

zip 안에 `사용법.txt`가 같이 들어간다. 요지는 이렇다.

- 압축을 풀고 `lyricsync.exe` 더블클릭. 설치 과정은 없다.
- **폴더 전체를 그대로 두고 써야 한다.** exe만 꺼내면 동작하지 않는다 (ffmpeg와 라이브러리가 옆에 있어야 한다).
- 처음 실행할 때 음성 인식 모델을 자동으로 내려받는다 (모델 크기에 따라 1~3GB, 인터넷 필요). 한 번 받으면 다음부터는 안 받는다.
- 윈도우 10/11 64비트.

### 빌드 설정에서 알아둘 것

| 결정 | 이유 |
|---|---|
| **onedir** (폴더 통째로) | onefile로 묶으면 실행할 때마다 2GB를 임시 폴더에 전부 풀어서 시작에 몇 분 걸린다. 이 크기에서는 쓸 수 없다. |
| **CPU 전용 torch** | CUDA 포함 휠은 2.5GB가 더 붙는데, 받는 사람이 NVIDIA GPU가 아니면 아무 의미가 없다. |
| **ffmpeg 번들** | 받는 사람 PC에 ffmpeg가 깔려 있다고 가정할 수 없다. CI가 내려받아 같이 묶는다. |
| **UPX 안 씀** | torch DLL을 UPX로 압축하면 깨지는 사례가 많다. |
| **`console=False`** | 콘솔 창이 깜빡이지 않는다. 대신 `sys.stdout`이 `None`이 되어 라이브러리 내부 `print` 한 줄이 앱을 죽일 수 있어서, `packaging/entry.py`에서 막아 뒀다. |

결과물은 **압축 약 200~300MB**다. torch를 빼서 이 크기가 됐다.

**SmartScreen 경고** — 코드 서명 인증서가 없어서 "Windows가 PC를 보호했습니다"가 뜬다. "추가 정보 → 실행"을 누르면 된다. 없애려면 유료 코드 서명 인증서(연 20~40만원)가 필요하다.

### 윈도우 PC에서 직접 빌드하려면

```bat
pip install torch torchaudio --index-url https://download.pytorch.org/whl/cpu
pip install faster-whisper demucs pyinstaller
:: packaging\ffmpeg\ 에 ffmpeg.exe, ffprobe.exe 를 넣어두고
pyinstaller packaging\lyricsync.spec --noconfirm --clean
```

`dist\lyricsync\` 폴더가 결과물이다.

---

## 창에서 쓰기

`lyricsync.exe`를 더블클릭하면 창이 뜬다. 소스에서는 `python -m lyricsync`.

창에서 할 수 있는 것: 음원·가사 선택(음원과 같은 이름의 `.txt`가 옆에 있으면 자동으로 집어준다), 출력 포맷 선택, 정확도·언어·싱크 보정, 보컬 분리 토글, 진행 로그, 작업 취소, 끝나고 저장 폴더 열기.

시작할 때 ffmpeg와 음성 인식 백엔드를 미리 확인해서, 문제가 있으면 실행 버튼을 잠그고 원인을 보여준다. 눌러보고 나서 알게 되면 원인을 짚기 어렵다.

**취소는 단계 경계에서만 듣는다.** 보컬 분리나 음성 인식 추론 도중에는 끊을 수 없어서, 진행 중인 단계가 끝난 뒤에 멈춘다. 창에 "취소 중…"이 뜨는 동안이 그 대기 시간이다.

## 명령줄에서 쓰기

```bash
# 가사 있음 — 가장 정확
python -m lyricsync song.mp3 --lyrics lyrics.txt -o song.lrc

# 가사 없음 — 받아쓰기까지
python -m lyricsync song.mp3 -o song.lrc

# 여러 포맷 한번에
python -m lyricsync song.mp3 --lyrics lyrics.txt -f lrc -f srt -f json

# 가라오케식 단어별 하이라이트
python -m lyricsync song.mp3 --lyrics lyrics.txt --word-level

# 전체가 0.3초 늦게 나왔을 때
python -m lyricsync song.mp3 --lyrics lyrics.txt --offset -0.3

# CPU에서 빠르게 (정확도 희생)
python -m lyricsync song.mp3 --no-separate --model small
```

가사 파일은 그냥 텍스트다. 빈 줄과 `[Verse 1]`, `(후렴)` 같은 섹션 표기는 자동으로 걸러진다. 인코딩은 UTF-8과 CP949(윈도우 메모장 기본) 둘 다 읽는다.

## 소스에서 돌리려면

```bash
sudo apt install ffmpeg        # 또는 brew install ffmpeg
pip install faster-whisper demucs
```

`whisperx`(정확도 최상)나 `openai-whisper`(공식 구현)도 쓸 수 있고, 설치된 것 중 가장 좋은 걸 자동으로 고른다.

## 결과 읽기

작업이 끝나면 **손봐야 할 구간**을 리포트한다. 이걸 보고 어디를 고칠지 판단하면 된다.

```
전체 42줄
확인 권장 3줄 (7%)
이 중 1줄은 음원에서 못 찾아 앞뒤 사이로 추정했습니다.

  [추정]   78.40s  conf 0.00  랩 구간 가사 한 줄
  [낮음]  102.10s  conf 0.33  영어 섞인 줄이야
```

- `[낮음]` — 매칭은 됐지만 일부 단어만 맞음. 시각이 조금 밀렸을 수 있다.
- `[추정]` — ASR이 그 줄을 아예 못 들음. 앞뒤 줄 사이에 비례 배분한 값이라 신뢰할 수 없다.

TSV로 뽑으면 엑셀에서 전체를 한눈에 훑으면서 고칠 수 있다.

### 신뢰도가 낮게 나오는 전형적 원인

1. **가사 파일이 이 음원 버전과 다름** — 라이브/리믹스/다른 편집본. 확인 권장이 40%를 넘으면 거의 이 경우다.
2. **랩, 빠른 구간, 애드립** — ASR이 못 따라간다.
3. **영어나 고유명사 혼재** — 언어를 '자동 감지'로 바꿔보면 나아질 때가 있다.
4. **보컬 분리를 끔** — 다시 켜고 돌린다.

## 정확도 현실치

한국 가요 기준 대략 이렇다. 곡에 따라 편차가 크다.

- **타임스탬프**: 가사를 주면 대부분 ±0.2초 안쪽. 글자가 좀 틀려도 "언제 불렀는지"는 잘 맞는다.
- **받아쓰기 글자** (가사 없이): 보컬 분리 + `large-v3` 기준 70~90%. 고유명사, 섞인 영어, 빠른 랩에서 자주 틀린다.

GUI 기본값은 `medium`이다. CPU 전용 빌드에서 `large-v3`는 3분 곡에 수십 분이 걸릴 수 있다. 공유받은 사람은 대개 GPU가 없으니 `medium`에서 시작하는 게 맞다.

## 구조

```
lyricsync/
  normalize.py   한글 자모 분해, 토큰 정규화
  align.py       Needleman-Wunsch 정렬, 보간, 단조성 보정
  audio.py       ffmpeg 전처리, Demucs 보컬 분리
  asr.py         Whisper 백엔드 3종 추상화
  formats.py     LRC / SRT / VTT / JSON / TSV
  pipeline.py    CLI·GUI 공용 작업 본체 (로그/단계/취소 콜백)
  runtime.py     번들 판별, ffmpeg 경로, 모델 캐시 위치
  cli.py         명령줄
  gui.py         tkinter 창
packaging/
  entry.py       exe 진입점 (스트림 보호, 크래시 로그)
  rthook_numpy.py  numpy를 다른 훅보다 먼저 적재
  lyricsync.spec PyInstaller 설정 (실패 기록이 주석에 있음)
  ffmpeg/        CI가 ffmpeg.exe를 여기 넣는다
.github/workflows/
  build-windows.yml  윈도우 자동 빌드 + 릴리스
tests/            57건
```

### 정렬이 동작하는 방식

가사 전체를 단어 토큰으로 평탄화하고, ASR 단어 시퀀스와 Needleman-Wunsch 전역 정렬을 돌린다. 각 토큰이 몇 번째 가사 줄 소속인지 기억해 두고, 매칭된 ASR 단어의 시각 중 최소/최대를 그 줄의 시작/끝으로 쓴다.

단어 비교는 **한글 자모 단위**로 한다. "있어"와 "이써"는 글자로는 완전히 다르지만 자모로 풀면 `ㅇㅣㅆㅇㅓ` vs `ㅇㅣㅆㅓ`로 꽤 가깝다. ASR이 받침에서 가장 많이 틀리기 때문에 이게 체감 차이가 크다.

아무 단어도 못 맞춘 줄은 버리지 않고 앞뒤로 확정된 줄 사이에 균등 배분하고 `interpolated` 표시를 남긴다. 마지막으로 전체를 훑어 시간이 뒤로 가거나 줄끼리 겹치지 않게 정리한다.

정렬 파라미터는 `align.py` 상단에 모아뒀다. 결과가 이상하면 거기부터 만지면 된다.

```python
GAP_PENALTY = -0.5      # 한쪽을 건너뛸 때 비용
SUB_BIAS = -0.8         # 엉뚱한 단어끼리 묶일 때 벌점
MIN_MATCH_SIM = 0.55    # 이 아래는 "맞춘 것"으로 안 침
```

## 테스트

```bash
python3 tests/test_align.py      # 정렬 코어 11건
python3 tests/test_formats.py    # 출력 포맷 11건
python3 tests/test_runtime.py    # 번들 런타임 8건
python3 tests/test_cli.py        # CLI 종단 8건
python3 tests/test_gui.py        # GUI 로직 17건
```

57건 전부 **Whisper 설치 없이** 돌아간다 (ffmpeg만 필요). `test_cli.py`와 `test_gui.py`는 가짜 ASR을 끼워 넣어 음원 입력부터 파일 출력까지 전 구간 배선을 확인한다. `test_gui.py`는 `stub_tk.py`로 tkinter를 대신해서 디스플레이 없이 창 조립과 입력 검증을 실행한다.

**GUI 외관은 테스트로 검증되지 않는다.** 위젯이 어떻게 보이는지, 한글이 안 깨지는지, 레이아웃이 좁지 않은지는 윈도우에서 직접 띄워 봐야 한다. 빌드가 끝나면 그걸 먼저 확인하는 게 좋다.

## 왜 exe에 보컬 분리가 없나

보컬 분리(Demucs)는 PyTorch를 끌고 오는데, 이걸 faster-whisper(CTranslate2)와 한 exe에 묶으면 윈도우에서 실행이 안 됐다. 네 번 시도한 기록은 `packaging/lyricsync.spec` 상단 주석에 남겼다. 요약하면 OpenMP 런타임이 두 벌 들어가고 numpy의 C 확장이 두 번 초기화되면서 앱이 아예 뜨지 않았다.

보컬 분리는 품질 향상 기능이지 필수가 아니다. 그리고 **주 사용 경로인 "가사 파일 함께 넣기"에서는 손실이 거의 없다** — 그 모드는 ASR에서 시간만 빌려오고 글자는 네 가사를 그대로 쓰기 때문에, 반주 때문에 인식이 좀 흔들려도 타임스탬프는 잘 맞는다. 손해는 가사 없이 받아쓰기할 때뿐이다.

소스로 돌릴 때는 `pip install demucs` 하면 그대로 쓸 수 있고, 프로그램이 알아서 감지한다.

## 알려진 한계

- **연주곡**은 당연히 안 된다.
- **동일 가사 반복이 많은 후렴**에서 정렬이 엉킬 수 있다. 전역 정렬이라 전체 순서는 지켜지지만, 어느 반복에 붙는지는 틀릴 수 있다.
- **듀엣/화음 구간**은 Demucs가 둘을 같은 보컬 트랙에 섞어놓기 때문에 ASR이 겹쳐 듣는다.
- **취소**는 단계 경계에서만 듣는다 (위 설명 참고).
- **확장 LRC 단어 타임스탬프**는 ASR이 실제로 매칭한 단어만 가진다. 보간된 줄은 줄 단위 타임스탬프만 나온다.
- LRC는 1/100초까지만 표현한다. 더 정밀한 값이 필요하면 JSON을 쓴다.

## 라이선스 / 저작권

도구 자체는 마음대로 써도 된다. 다만 **상용 음원의 가사를 추출해 배포하는 건 저작권 문제가 될 수 있다.** 본인 소유 음원의 개인적 사용, 자작곡, 또는 권리를 가진 콘텐츠에 쓰는 걸 전제로 만들었다. 공개 저장소에 올려 배포한다면 이 점을 받는 사람에게도 알려주는 게 좋다.

번들에 포함되는 것들의 라이선스도 따라온다: ffmpeg(LGPL/GPL), PyTorch(BSD), Demucs(MIT), faster-whisper(MIT), Whisper 모델(MIT). GPL 빌드 ffmpeg를 묶으면 전체가 GPL 조건을 받으므로, 워크플로는 LGPL 기반 essentials 빌드를 쓴다.
