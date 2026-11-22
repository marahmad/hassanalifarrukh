# Hassan Ali Farrukh AI Digital Twin & WhatsApp Assistant

An end-to-end AI assistant trained on **253 Instagram reels** of **Hassan Ali Farrukh (@hassanalifarrukh)**. Built to handle follower queries on WhatsApp with his signature discipline, ex-SSG security mindset, and Roman Urdu phrasing.

---

## 🚀 Quick Setup Guide

### 1. Install Dependencies
```bash
pip install -r requirements.txt
```

### 2. Configure Environment Variables
Copy `.env.example` to `.env`:
```bash
cp .env.example .env
```
Fill in your keys:
* `GROQ_API_KEY`: For ultra-fast Whisper transcription (from [console.groq.com](https://console.groq.com)).
* `GEMINI_API_KEY`: For AI response generation (from Google AI Studio).
* `WHATSAPP_TOKEN` & `WHATSAPP_PHONE_NUMBER_ID`: (Optional, when connecting to Meta Cloud API).

---

## 🛠️ Step-by-Step Workflow

### Step 1: Transcribe the 253 Downloaded Reels
Extracts 16kHz audio using FFmpeg and calls Groq Whisper API:
```bash
python -m src.transcribe
```
Transcripts will be saved with segment timestamps and captions into `data/transcripts/`.

### Step 2: Build the ChromaDB Vector Knowledge Base
Chunks and indexes all transcripts:
```bash
python -m src.knowledge_base
```

### Step 3: Test Locally in Your Terminal
You can chat with Hassan Ali Farrukh immediately without connecting to WhatsApp:
```bash
python -m src.test_bot_local
```

### Step 4: Launch the WhatsApp Meta Webhook Server
Starts the FastAPI server with Meta verification, Free Trial (3 questions), and Easypaisa/JazzCash TRX ID handlers:
```bash
python -m src.server
```

---

## 💼 Business & Monetization Architecture

* **Pricing:** PKR 1,500 / month per user.
* **Trial:** 3 free questions per new phone number before the paywall triggers.
* **Payments:** Easypaisa, JazzCash, Raast.
* **Admin Verification:** When a user sends `TRX: <id>`, an alert is sent to your admin number. You simply reply `/activate <phone_number> 30` to grant 30 days of access.

---

## 📑 Strategic Documents

* **Full Technical & Financial Blueprint:** Check [`hassan_ali_farrukh_ai_blueprint.md`](file:///C:/Users/games/.gemini/antigravity-ide/brain/ed8ec163-08e6-4c7d-99b2-5fbea8c47a9b/hassan_ali_farrukh_ai_blueprint.md)
* **Partnership Proposal & Pitch Templates (50/50 Split):** Check [`hassan_ali_farrukh_partnership_pitch.md`](file:///C:/Users/games/.gemini/antigravity-ide/brain/ed8ec163-08e6-4c7d-99b2-5fbea8c47a9b/hassan_ali_farrukh_partnership_pitch.md)
