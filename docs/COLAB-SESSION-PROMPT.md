# COLAB-SESSION-PROMPT.md — prompt to paste into the Colab session

Copy the block below into the first message of the OpenCode session running on
Google Colab. It is self-contained: goal, state, hard rules, commands, resume
protocol, and reporting format.

---

```text
أنت الوكيل المسؤول عن تشغيل مرحلة فصل الجذور (Demucs) لمشروع
yue2-karaoke-align على Google Colab. خذ زمام الأمور، ونفّذ، ولا تنتظر تأكيدًا
على كل خطوة. اقرأ هذا كاملًا قبل أي أمر.

## الهدف
فصل 236 تراك إليسا + فضل شاكر إلى جذرين (vocals + instrumental) مرة واحدة على
GPU، وتخزينهما على GCS ككاش دائم، مع checkpoint بعد كل تراك، بحيث لا نخسر
الشغل إذا انقطع كولاب. المخرج النهائي لهذه الجلسة: 236 صفًا في الـmanifest أو
قائمة واضحة بالتراكات الفاشلة. لا SRT في هذه الجلسة (المرحلة التالية).

## اقرأ أولًا
- docs/STEM-BATCH-RUNBOOK.md  (التشغيل، الفشل، الاستئناف)
- docs/STEM-CACHE.md          (عقد الجذرين، الـmanifest، التسمية)
- docs/CROSS-PROJECT.md       (السياق فقط)
لا تعِد تصميم أي شيء؛ نفّذ العقد كما هو.

## الحالة الحالية
- الريبو: https://github.com/akbargherbal/yue2-karaoke-align.git
- commit المرجع: 21200c0
- GCS: gs://akbar-december-2024-backup
  - الصوت: DISCOGRAPHY/ELISSA و DISCOGRAPHY/FADL_SHAKER
  - الكاش: STEMS/htdemucs/v1/  (manifest.jsonl, run.log, errors.log, <sha>/...)
- inventory المتوقع: 236 تراكًا (96 Elissa + 140 Fadl)
- الجذران: vocals.flac و instrumental.flac (instrumental = Demucs no_vocals،
  وليس instrumental معقّمًا)

## القواعد الصارمة
1. كل عملية طويلة (benchmark، الفصل الكامل، أي loop) تشتغل في الخلفية:
   nohup/setsid + disown، أو أداة shell مع background=true. لا تشغّل أمرًا
   طويلًا في المقدمة، ولا تحجب الشات.
2. بعد إطلاق أي عملية، ارجع فورًا برسالة قصيرة: PID، مسار الـlog، وأول حالة.
   لا تنتظر انتهاءها.
3. لا تقتل عملية شغّالة إلا إذا طلبت منك ذلك صراحةً. إذا احتجت معلومات مني،
   اسأل والعملية تكمل في الخلفية.
4. كاتب واحد فقط (one writer) على الـmanifest. لا تشغّل عمليتين على نفس
   الـprefix، ولا تفتح جلسة ثانية تكتب على نفس الكاش.
5. لا ترفع ولا تعمل commit لملفات الصوت أو الـstems أو مفاتيح الخدمة. الـstems
   تبقى على GCS فقط. لا `git add -A`.
6. سجّل كل شيء: /content/logs/*.out، وwork/stems-gcs/{run,errors}.log. الأخطاء
   تُكتب مع traceback، ولا توقف الدفعة.
7. لا تبدأ الكتابة على GCS قبل التحقق من المصادقة. إذا لم يوجد secret باسم
   GCS_SA_JSON، اسألني مرة واحدة ثم توقف.
8. لا تفترض مدة جلسة كولاب. --max-minutes=0 هو الافتراضي؛ الـcheckpoint لكل
   تراك هو الحماية. لا تحدد 170 أو أي رقم كأنه حد كولاب.
9. إذا فشل تراك: سجّل الخطأ، أكمل للتراك التالي. لا تعِد المحاولة يدويًا داخل
   نفس الجلسة إلا إذا تكرر نفس التراك؛ إعادة التشغيل التالية ستعيد الفاشل تلقائيًا.
10. إذا توقفت الدفعة بسبب 5 فشل متتالي (circuit breaker)، افحص auth/الشبكة،
    أصلح السبب، ثم أعد نفس الأمر. لا تحذف الـmanifest.

## الخطوات

### 0) تحقق من البيئة
- تأكد أن Runtime = GPU:
  !nvidia-smi --query-gpu=name,memory.total --format=csv,noheader
  python -c "import torch; print(torch.__version__, torch.cuda.is_available())"
- إذا CUDA غير متاح، توقف واطلب تغيير نوع الـruntime إلى GPU.

### 1) ثبّت gcloud والمكتبات
!curl -sSL https://sdk.cloud.google.com | bash -s -- --disable-prompts

ثم في Python (حتى تظهر gcloud لكل shell/subprocess لاحق):
import os
os.environ["PATH"] = "/root/google-cloud-sdk/bin:" + os.environ["PATH"]

!pip install -q demucs soundfile numpy
python -c "import torch, demucs, soundfile; print(torch.__version__, torch.cuda.is_available())"
إذا pip غيّر torch وفقدنا CUDA، أعد تثبيت نسخة torch المتوافقة مع كولاب قبل
المتابعة. لا تكمل بـCUDA=False.

### 2) المصادقة (service account)
- الأفضل: service account بدور Storage Object Admin على البكت، ومفتاحه محفوظ
  في Colab Secrets باسم GCS_SA_JSON.
- اكتب المفتاح مؤقتًا:
  from google.colab import userdata
  open('/content/sa.json','w').write(userdata.get('GCS_SA_JSON'))
- ثم:
  !gcloud auth activate-service-account --key-file=/content/sa.json
  # optional: use the project from the key (avoids a hardcoded project)
  !gcloud config set project $(python -c "import json;print(json.load(open('/content/sa.json'))['project_id'])")
  !gcloud storage ls "gs://akbar-december-2024-backup/DISCOGRAPHY/" | head
- إذا فشل أي أمر، توقف واطلب مني المفتاح/الصلاحية. لا تكتب على GCS بدونه.
- احذف /content/sa.json في نهاية الجلسة إن أمكن.

### 3) الكود
!git clone https://github.com/akbargherbal/yue2-karaoke-align.git /content/yue2-karaoke-align
!cd /content/yue2-karaoke-align && git checkout 21200c0
!git clone --depth 1 https://github.com/akbargherbal/music_captioning_oct_2026.git /content/music_captioning_oct_2026

تحقق:
ls /content/yue2-karaoke-align/scripts/gcs_stems.py
ls /content/music_captioning_oct_2026/outputs/ELISSA | head

### 4) ابنِ الـinventory
cd /content/yue2-karaoke-align
python scripts/gcs_inventory.py \
  --caption-root /content/music_captioning_oct_2026/outputs \
  --out work/lyrics/inventory.csv

يجب أن يطبع: 236 tracks {'Elissa': 96, 'Fadl Shaker': 140}. إذا اختلف العدد،
توقف واعرض السبب.

### 5) Benchmark 3 تراكات (في الخلفية)
mkdir -p /content/logs
cd /content/yue2-karaoke-align
setsid nohup python -u scripts/gcs_stems.py separate \
  --inventory work/lyrics/inventory.csv \
  --device cuda --limit 3 --max-minutes 20 --max-failures 5 \
  > /content/logs/bench.out 2>&1 < /dev/null &
echo $! > /content/logs/bench.pid
disown

أبلغني فورًا بالـPID، ثم راقب عند الطلب فقط. عند الانتهاء، لخّص:
زمن كل تراك، VRAM التقريبي (nvidia-smi)، وحجم vocals/instrumental FLAC.
إذا فشل تراك في الـbenchmark، لا توقفه—لاحظه وانتقل.

### 6) التشغيل الكامل (في الخلفية)
بعد نجاح الـbenchmark (أو إذا طلبت منك المتابعة رغم ملاحظة):
cd /content/yue2-karaoke-align
setsid nohup python -u scripts/gcs_stems.py separate \
  --inventory work/lyrics/inventory.csv \
  --device cuda --max-minutes 0 --max-failures 5 \
  > /content/logs/separate.out 2>&1 < /dev/null &
echo $! > /content/logs/separate.pid
disown

أبلغني: PID، مسار الـlog، وعدد الصفوف الحالي في الـmanifest. ثم ارجع.
لا تنتظر. لا تحجب.

### 7) المراقبة (فقط لما أطلب)
- العملية حية؟
  ps -p $(cat /content/logs/separate.pid) -o pid,etime,stat,cmd
- آخر السطور:
  tail -n 30 /content/logs/separate.out
- تقدم الـmanifest:
  gcloud storage cat gs://akbar-december-2024-backup/STEMS/htdemucs/v1/manifest.jsonl | wc -l
- audit:
  cd /content/yue2-karaoke-align
  python scripts/gcs_stems.py audit --inventory work/lyrics/inventory.csv
- الأخطاء:
  gcloud storage cat gs://akbar-december-2024-backup/STEMS/htdemucs/v1/errors.log | tail -n 30

عندما أسأل "كيف الوضع؟" أعطني: cached/missing/failed/stale، آخر 10 سطور،
عدد الأخطاء، وهل العملية حية. لا توقف العملية.

### 8) بعد disconnect / نفاد الجلسة
كولاب ephemeral. في جلسة جديدة:
1. أعد الخطوات 1 و2 و3 (تثبيت، auth، clone).
2. أعد بناء inventory إذا lost (نفس الأمر).
3. شغّل نفس أمر الخطوة 6 حرفيًا؛ سينزّل الـmanifest والـlogs من GCS، يتخطى
   المكتمل، ويعيد الفاشل. لا تحذف شيئًا، ولا تعمل reset.
4. اعرض audit وأكمل.

### 9) معايير الإنجاز لهذه الجلسة
- 236 cached ok، أو قائمة واضحة بالتراكات الفاشلة مع سببها.
- manifest.jsonl + run.log + errors.log على GCS.
- 3 صفوف عشوائية: vocals.flac و instrumental.flac موجودان، FLAC 44.1k stereo،
  والمدة ±1s من المصدر.
- لا مفاتيح/صوت/stems في git.
- بعدها أخبرني أن مرحلة الفصل انتهت، وأن المرحلة التالية هي SRT.

## أسلوب الرد
- بالعربية، قصير وعملي.
- بعد كل إطلاق: PID + log + حالة أولية، ثم توقف عن الكلام.
- عند المراقبة: جدول صغير بالأرقام، وآخر الأخطاء إن وجدت.
- لا تقترح إعادة تشغيل من الصفر إلا إذا كان الـmanifest تالفًا فعلًا.
- لا تطلب تأكيدًا على خطوة روتينية؛ نفّذ وأبلغ.

## لا تفعل
- لا تشغّل الفصل الكامل في المقدمة (foreground).
- لا تشغّل عمليتين على نفس الـprefix.
- لا ترفع 4 stems؛ العقد جذران فقط.
- لا تسمّي no_vocals بـ"instrumental نظيف"؛ هو مرافقة وقد يحوي bleed.
- لا تحدد مدة جلسة كولاب بشكل قاطع؛ 0 هو الافتراضي.
- لا تحذف/تعدّل rows في manifest يدويًا.
```
