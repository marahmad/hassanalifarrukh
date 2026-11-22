# Importing This Skill Into Another Workspace

You can easily copy and use this media sync skill in any other Antigravity workspace or standalone project.

---

### Step 1: Copy the Skill Directory
Copy the entire folder:
```
d:\hassanalifarrukh\.agents\skills\instagram-media-sync\
```
To your other project directory:
```
<YOUR_OTHER_WORKSPACE>\.agents\skills\instagram-media-sync\
```

---

### Step 2: Copy Required Files
From this workspace, copy:
1. `src/sync.py` -> `<YOUR_OTHER_WORKSPACE>/src/sync.py`
2. `cookies.txt` -> `<YOUR_OTHER_WORKSPACE>/cookies.txt`

---

### Step 3: Configure Environment Variables
In `<YOUR_OTHER_WORKSPACE>/.env`, ensure you have:
```env
GROQ_API_KEY="your-groq-api-key"
```

---

### Step 4: Install Dependencies
```bash
pip install gallery-dl groq chromadb python-dotenv
```

---

### Step 5: Execute
Run the sync anytime in your other workspace:
```bash
python .agents/skills/instagram-media-sync/scripts/sync.py
```
Or with specific options:
```bash
python .agents/skills/instagram-media-sync/scripts/sync.py --stories-only
python .agents/skills/instagram-media-sync/scripts/sync.py --reels-only
```

