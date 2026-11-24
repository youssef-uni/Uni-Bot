import os
import time
import logging
import requests
import xml.etree.ElementTree as ET
from io import BytesIO
from pypdf import PdfReader

# =========================================================
# Uni-Bot - بوت الجامعة
# ضع TELEGRAM_BOT_TOKEN و GEMINI_API_KEY في متغيرات البيئة
# ولا تضع مفاتيحك السرية داخل الملف أو تشاركها مع أحد.
# =========================================================

TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
GEMINI_KEY = os.getenv("GEMINI_API_KEY", "").strip()
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash").strip()

TELEGRAM_API = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}"
GEMINI_API = (
    f"https://generativelanguage.googleapis.com/v1beta/"
    f"models/{GEMINI_MODEL}:generateContent"
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s"
)

# جلسات المستخدمين مؤقتة: تُمسح عند إعادة تشغيل البرنامج.
user_modes = {}
user_colleges = {}

MAIN_KEYBOARD = {
    "keyboard": [
        [{"text": "🎓 الكليات"}, {"text": "🔬 البحث العلمي"}],
        [{"text": "📄 PDF والملخصات"}, {"text": "🧪 التدريب العملي"}],
        [{"text": "📅 مخطط الدراسة"}]
    ],
    "resize_keyboard": True
}

BACK_KEYBOARD = {
    "keyboard": [[{"text": "🏠 القائمة الرئيسية"}]],
    "resize_keyboard": True
}

COLLEGES = [
    "هندسة", "طب", "علوم الحاسب", "ذكاء اصطناعي",
    "نظم المعلومات", "تجارة", "حقوق", "علوم", "آداب"
]


def telegram_request(method, payload=None, files=None, timeout=60):
    """إرسال طلب إلى Telegram والتحقق من نجاحه."""
    if not TELEGRAM_TOKEN:
        raise RuntimeError("لم يتم ضبط TELEGRAM_BOT_TOKEN.")
    url = f"{TELEGRAM_API}/{method}"
    response = requests.post(
        url, data=payload, files=files, timeout=timeout
    )
    response.raise_for_status()
    result = response.json()
    if not result.get("ok"):
        raise RuntimeError(result.get("description", "Telegram API error"))
    return result.get("result")


def send_message(chat_id, text, keyboard=None):
    """إرسال الرسائل الطويلة على أجزاء."""
    text = str(text or "").strip()
    if not text:
        text = "لم أتمكن من إعداد رد."
    chunks = [text[i:i + 3900] for i in range(0, len(text), 3900)]
    for index, chunk in enumerate(chunks):
        payload = {"chat_id": chat_id, "text": chunk}
        if index == len(chunks) - 1 and keyboard:
            import json
            payload["reply_markup"] = json.dumps(
                keyboard, ensure_ascii=False
            )
        telegram_request("sendMessage", payload=payload)


def ask_gemini(prompt):
    """إرسال سؤال إلى Gemini وإرجاع النص."""
    if not GEMINI_KEY:
        return "مفتاح Gemini غير مضبوط. أضف GEMINI_API_KEY في إعدادات التشغيل."
    try:
        response = requests.post(
            GEMINI_API,
            params={"key": GEMINI_KEY},
            json={
                "contents": [
                    {"parts": [{"text": prompt}]}
                ],
                "generationConfig": {
                    "temperature": 0.6,
                    "maxOutputTokens": 1800
                }
            },
            timeout=90
        )
        response.raise_for_status()
        data = response.json()
        return (
            data["candidates"][0]["content"]["parts"][0]["text"].strip()
        )
    except requests.RequestException as exc:
        logging.exception("Gemini request failed")
        return f"حصلت مشكلة في الاتصال بخدمة الذكاء الاصطناعي: {exc}"
    except (KeyError, IndexError, TypeError):
        logging.exception("Unexpected Gemini response")
        return "وصلني رد غير متوقع من خدمة الذكاء الاصطناعي. حاول مرة أخرى."


def search_pubmed(query, limit=5):
    """البحث في PubMed وإرجاع عناوين وملخصات مختصرة."""
    try:
        search_url = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi"
        search_response = requests.get(
            search_url,
            params={
                "db": "pubmed",
                "term": query,
                "retmax": limit,
                "retmode": "json"
            },
            timeout=30
        )
        search_response.raise_for_status()
        ids = search_response.json().get("esearchresult", {}).get("idlist", [])
        if not ids:
            return "لم أعثر على نتائج في PubMed لهذا البحث."

        summary_url = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esummary.fcgi"
        summary_response = requests.get(
            summary_url,
            params={
                "db": "pubmed",
                "id": ",".join(ids),
                "retmode": "json"
            },
            timeout=30
        )
        summary_response.raise_for_status()
        records = summary_response.json().get("result", {})
        output = ["نتائج من PubMed:"]
        for pmid in ids:
            item = records.get(pmid, {})
            title = item.get("title", "عنوان غير متاح")
            year = item.get("pubdate", "السنة غير متاحة")
            output.append(
                f"• {title}\nالسنة: {year}\n"
                f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/"
            )
        return "\n\n".join(output)
    except requests.RequestException as exc:
        logging.exception("PubMed search failed")
        return f"تعذر الاتصال بـ PubMed: {exc}"


def search_arxiv(query, limit=5):
    """البحث في arXiv وإرجاع العناوين والروابط."""
    try:
        response = requests.get(
            "https://export.arxiv.org/api/query",
            params={
                "search_query": f"all:{query}",
                "start": 0,
                "max_results": limit
            },
            timeout=30
        )
        response.raise_for_status()
        root = ET.fromstring(response.text)
        namespace = {"atom": "http://www.w3.org/2005/Atom"}
        entries = root.findall("atom:entry", namespace)
        if not entries:
            return "لم أعثر على نتائج في arXiv لهذا البحث."

        output = ["نتائج من arXiv:"]
        for entry in entries:
            title = (entry.findtext("atom:title", default="", namespaces=namespace)
                     or "عنوان غير متاح").strip().replace("\n", " ")
            link = entry.findtext("atom:id", default="", namespaces=namespace)
            output.append(f"• {title}\n{link}")
        return "\n\n".join(output)
    except (requests.RequestException, ET.ParseError) as exc:
        logging.exception("arXiv search failed")
        return f"تعذر البحث في arXiv: {exc}"


def scientific_research(query):
    return (
        search_pubmed(query) + "\n\n" +
        search_arxiv(query)
    )


def download_telegram_file(file_id):
    """تنزيل ملف من Telegram وإرجاع محتواه واسمه."""
    file_info = telegram_request(
        "getFile", payload={"file_id": file_id}
    )
    file_path = file_info["file_path"]
    url = f"https://api.telegram.org/file/bot{TELEGRAM_TOKEN}/{file_path}"
    response = requests.get(url, timeout=90)
    response.raise_for_status()
    return response.content, os.path.basename(file_path)


def summarize_pdf(pdf_bytes):
    """استخراج نص PDF ثم تلخيصه باستخدام Gemini."""
    try:
        reader = PdfReader(BytesIO(pdf_bytes))
        page_text = []
        for page in reader.pages[:40]:
            page_text.append(page.extract_text() or "")
        full_text = "\n".join(page_text).strip()

        if not full_text:
            return (
                "لم أستطع استخراج نص من الملف. قد يكون PDF عبارة عن صور "
                "ويحتاج إلى OCR."
            )

        full_text = full_text[:25000]
        prompt = (
            "لخّص محتوى ملف PDF التالي باللغة العربية بطريقة منظمة للطالب. "
            "استخرج الفكرة الرئيسية، أهم النقاط، المصطلحات، ثم أسئلة مراجعة "
            "قصيرة. لا تضف معلومات غير موجودة في النص.\n\n"
            f"محتوى الملف:\n{full_text}"
        )
        return ask_gemini(prompt)
    except Exception as exc:
        logging.exception("PDF processing failed")
        return f"لم أستطع قراءة ملف PDF: {exc}"


def college_prompt(college, question):
    return (
        f"أنت مساعد جامعي متخصص في كلية {college}. "
        "اشرح للطالب بالعربية بوضوح، وقدّم أمثلة عملية مناسبة للتخصص. "
        "إذا كان السؤال يحتاج معلومات غير مؤكدة فوضّح ذلك.\n\n"
        f"سؤال الطالب: {question}"
    )


def practical_training(college, request_text):
    return ask_gemini(
        f"أنت مدرب عملي جامعي في تخصص {college}. "
        "صمّم تدريبًا عمليًا تفاعليًا خطوة بخطوة، مع مهمة للطالب "
        "ومعايير لتقييم إجابته. لا تفترض أنه نفّذ خطوة لم ينفذها.\n\n"
        f"طلب الطالب: {request_text}"
    )


def make_study_plan(request_text):
    return ask_gemini(
        "أنت مساعد لتنظيم الدراسة. أنشئ خطة واقعية باللغة العربية "
        "في جدول يومي أو أسبوعي حسب المعلومات التي يقدمها الطالب. "
        "إذا كانت المواعيد أو المواد ناقصة، اذكر الافتراضات بوضوح.\n\n"
        f"تفاصيل الطالب: {request_text}"
    )


def handle_message(message):
    chat_id = message["chat"]["id"]
    user_id = message["from"]["id"]
    text = (message.get("text") or "").strip()

    if text in ("/start", "/menu", "🏠 القائمة الرئيسية"):
        user_modes.pop(user_id, None)
        user_colleges.pop(user_id, None)
        send_message(
            chat_id,
            "أهلًا بك في Uni-Bot 🎓\n"
            "بوت الجامعة الشامل. اختر القسم الذي تريد استخدامه:",
            MAIN_KEYBOARD
        )
        return

    if text == "🎓 الكليات":
        user_modes[user_id] = "college"
        rows = [[{"text": name}] for name in COLLEGES]
        rows.append([{"text": "🏠 القائمة الرئيسية"}])
        send_message(
            chat_id,
            "اختر كليتك أو تخصصك:",
            {"keyboard": rows, "resize_keyboard": True}
        )
        return

    if text in COLLEGES:
        user_colleges[user_id] = text
        user_modes[user_id] = "college_question"
        send_message(
            chat_id,
            f"تم اختيار {text}. اكتب سؤالك الدراسي:",
            BACK_KEYBOARD
        )
        return

    if text == "🔬 البحث العلمي":
        user_modes[user_id] = "research"
        send_message(
            chat_id,
            "اكتب موضوع البحث أو الكلمات المفتاحية، وسأبحث في PubMed وarXiv.",
            BACK_KEYBOARD
        )
        return

    if text == "📄 PDF والملخصات":
        user_modes[user_id] = "pdf"
        send_message(
            chat_id,
            "أرسل ملف PDF هنا. سأستخرج النص وأعدّ ملخصًا إذا كان الملف قابلًا لاستخراج النص.",
            BACK_KEYBOARD
        )
        return

    if text == "🧪 التدريب العملي":
        user_modes[user_id] = "training"
        college = user_colleges.get(user_id, "التخصص الجامعي")
        send_message(
            chat_id,
            f"اكتب المهارة أو الموضوع الذي تريد التدريب عليه في {college}:",
            BACK_KEYBOARD
        )
        return

    if text == "📅 مخطط الدراسة":
        user_modes[user_id] = "planner"
        send_message(
            chat_id,
            "اكتب المواد والمواعيد المتاحة وموعد الامتحان أو المهام المطلوبة.",
            BACK_KEYBOARD
        )
        return

    mode = user_modes.get(user_id)

    if message.get("document") and mode == "pdf":
        document = message["document"]
        filename = document.get("file_name", "")
        if not filename.lower().endswith(".pdf"):
            send_message(chat_id, "من فضلك أرسل ملفًا بامتداد PDF.", BACK_KEYBOARD)
            return
        send_message(chat_id, "استلمت الملف. جارٍ استخراج النص وإعداد الملخص...")
        try:
            file_bytes, _ = download_telegram_file(document["file_id"])
            summary = summarize_pdf(file_bytes)
            send_message(chat_id, summary, MAIN_KEYBOARD)
        except Exception as exc:
            logging.exception("File handling failed")
            send_message(chat_id, f"حدث خطأ أثناء معالجة الملف: {exc}", MAIN_KEYBOARD)
        user_modes.pop(user_id, None)
        return

    if not text:
        send_message(chat_id, "أرسل نصًا أو اختر قسمًا من القائمة.", MAIN_KEYBOARD)
        return

    if mode == "research":
        send_message(chat_id, "جارٍ البحث في المصادر العلمية...")
        result = scientific_research(text)
        send_message(chat_id, result, MAIN_KEYBOARD)
    elif mode == "college_question":
        college = user_colleges.get(user_id, "التخصص الجامعي")
        send_message(chat_id, ask_gemini(college_prompt(college, text)), MAIN_KEYBOARD)
    elif mode == "training":
        college = user_colleges.get(user_id, "التخصص الجامعي")
        send_message(chat_id, practical_training(college, text), MAIN_KEYBOARD)
    elif mode == "planner":
        send_message(chat_id, make_study_plan(text), MAIN_KEYBOARD)
    else:
        send_message(
            chat_id,
            "اختر قسمًا من القائمة أولًا، أو اكتب /start لعرض القائمة.",
            MAIN_KEYBOARD
        )


def main():
    if not TELEGRAM_TOKEN:
        raise SystemExit(
            "خطأ: أضف متغير البيئة TELEGRAM_BOT_TOKEN قبل التشغيل."
        )

    # إزالة webhook حتى يعمل polling.
    try:
        telegram_request("deleteWebhook", payload={"drop_pending_updates": "true"})
    except Exception:
        logging.exception("Could not delete webhook")

    logging.info("Uni-Bot is starting...")
    offset = None

    while True:
        try:
            payload = {"timeout": 25}
            if offset is not None:
                payload["offset"] = offset
            updates = telegram_request(
                "getUpdates", payload=payload, timeout=35
            )
            for update in updates:
                offset = update["update_id"] + 1
                message = update.get("message")
                if message:
                    try:
                        handle_message(message)
                    except Exception:
                        logging.exception("Message handling error")
                        chat_id = message.get("chat", {}).get("id")
                        if chat_id:
                            try:
                                send_message(
                                    chat_id,
                                    "حصل خطأ أثناء تنفيذ الطلب. جرّب مرة أخرى.",
                                    MAIN_KEYBOARD
                                )
                            except Exception:
                                logging.exception("Could not send error message")
        except KeyboardInterrupt:
            logging.info("Uni-Bot stopped.")
            break
        except Exception:
            logging.exception("Polling error")
            time.sleep(5)


if __name__ == "__main__":
    main()
