import makeWASocket, {
  DisconnectReason,
  downloadMediaMessage,
  useMultiFileAuthState
} from "@whiskeysockets/baileys";
import qrcodeTerminal from "qrcode-terminal";
import QRCode from "qrcode";
import fs from "fs";
import path from "path";
import pino from "pino";

const PYTHON_API_URL = "http://127.0.0.1:8080/api/chat";
const PYTHON_TRANSCRIBE_URL = "http://127.0.0.1:8080/api/chat/transcribe";
const QR_HTML_PATH = path.resolve("./qr.html");

function loadBridgeSecret() {
  if (process.env.WHATSAPP_BRIDGE_SECRET) {
    return process.env.WHATSAPP_BRIDGE_SECRET.trim();
  }
  try {
    const envPath = path.resolve("../.env");
    if (fs.existsSync(envPath)) {
      const lines = fs.readFileSync(envPath, "utf-8").split(/\r?\n/);
      for (const line of lines) {
        const trimmed = line.trim();
        if (!trimmed || trimmed.startsWith("#")) continue;
        const eqIdx = trimmed.indexOf("=");
        if (eqIdx > 0) {
          const key = trimmed.slice(0, eqIdx).trim();
          const val = trimmed.slice(eqIdx + 1).trim().replace(/^["']|["']$/g, "");
          if (key === "WHATSAPP_BRIDGE_SECRET") return val;
        }
      }
    }
  } catch (e) {
    console.warn("[Bridge Warning] Could not read ../.env for WHATSAPP_BRIDGE_SECRET:", e.message);
  }
  return "";
}

const BRIDGE_SECRET = loadBridgeSecret();
if (!BRIDGE_SECRET) {
  console.warn("\n[SECURITY WARNING] WHATSAPP_BRIDGE_SECRET is not set in .env! API calls to Python will be rejected (401).\n");
}

async function startWhatsAppBot() {
  const { state, saveCreds } = await useMultiFileAuthState("auth_session");

  const sock = makeWASocket({
    auth: state,
    logger: pino({ level: "silent" })
  });

  sock.ev.on("creds.update", saveCreds);

  sock.ev.on("connection.update", async (update) => {
    const { connection, lastDisconnect, qr } = update;

    if (qr) {
      console.log("\n=======================================================");
      console.log("   SCAN THIS QR CODE IN WHATSAPP TO CONNECT THE BOT");
      console.log("   (WhatsApp -> Linked Devices -> Link a Device)");
      console.log("=======================================================\n");

      // 1. Terminal ASCII QR
      qrcodeTerminal.generate(qr, { small: true });

      // 2. Generate HTML browser QR for easy crystal-clear scanning
      try {
        const qrDataUrl = await QRCode.toDataURL(qr, { width: 360, margin: 2 });
        const html = `<!DOCTYPE html>
<html>
<head>
  <title>WhatsApp Bot QR Scan</title>
  <meta http-equiv="refresh" content="15">
  <style>
    body { font-family: sans-serif; display: flex; flex-direction: column; align-items: center; justify-content: center; height: 100vh; background: #0b141a; color: white; margin: 0; }
    .card { background: #111b21; padding: 30px; border-radius: 12px; text-align: center; box-shadow: 0 4px 20px rgba(0,0,0,0.5); }
    img { border-radius: 8px; margin: 15px 0; }
    p { color: #8696a0; font-size: 14px; margin: 4px 0; }
  </style>
</head>
<body>
  <div class="card">
    <h2>Hassan Ali Farrukh AI Bot</h2>
    <p>1. Open WhatsApp on your phone</p>
    <p>2. Tap Menu / Settings &gt; <b>Linked Devices</b></p>
    <p>3. Tap <b>Link a Device</b> and point your camera here</p>
    <img src="${qrDataUrl}" alt="Scan QR Code" />
  </div>
</body>
</html>`;
        fs.writeFileSync(QR_HTML_PATH, html, "utf-8");
        console.log(`[TIP] You can also open this file in your browser to scan:\nfile:///${QR_HTML_PATH.replace(/\\/g, "/")}\n`);
      } catch (e) {
        console.error("Error saving QR HTML:", e);
      }
    }

    if (connection === "close") {
      const statusCode = lastDisconnect?.error?.output?.statusCode;
      const shouldReconnect = statusCode !== DisconnectReason.loggedOut;
      console.log(`Connection closed (status: ${statusCode}). Reconnecting: ${shouldReconnect}`);
      if (shouldReconnect) {
        startWhatsAppBot();
      }
    } else if (connection === "open") {
      console.log("\n=======================================================");
      console.log("   [SUCCESS] Connected to WhatsApp!");
      console.log("   Hassan Ali Farrukh AI Twin is now active & replying!");
      console.log("=======================================================\n");
    }
  });

  sock.ev.on("messages.upsert", async (m) => {
    try {
      const msg = m.messages[0];
      if (!msg.message || msg.key.fromMe) return;

      const remoteJid = msg.key.remoteJid;
      // Skip group messages
      if (remoteJid.endsWith("@g.us")) return;

      const senderNumber = remoteJid.replace("@s.whatsapp.net", "");
      let incomingText =
        msg.message.conversation ||
        msg.message.extendedTextMessage?.text ||
        "";

      // Support WhatsApp Voice Notes via existing Groq Whisper /api/chat/transcribe endpoint
      if (!incomingText.trim() && msg.message.audioMessage) {
        try {
          const audioBuffer = await downloadMediaMessage(
            msg,
            "buffer",
            {},
            { logger: pino({ level: "silent" }), reuploadRequest: sock.updateMediaMessage }
          );
          const mime = (msg.message.audioMessage.mimetype || "audio/ogg").split(";")[0];
          const trRes = await fetch(PYTHON_TRANSCRIBE_URL, {
            method: "POST",
            headers: {
              "Content-Type": mime,
              "X-Bridge-Secret": BRIDGE_SECRET
            },
            body: audioBuffer
          });
          if (trRes.ok) {
            const trData = await trRes.json();
            incomingText = (trData.text || "").trim();
            console.log(`\n[WHATSAPP VOICE TRANSCRIBED] +${senderNumber}: "${incomingText}"`);
          } else {
            await sock.sendMessage(remoteJid, {
              text: "Voice note samajh nahi aaya — thoda saaf bol ke dobara bhej dein ya text likh dein."
            });
            return;
          }
        } catch (audioErr) {
          console.error("Voice note download/transcribe error:", audioErr);
          await sock.sendMessage(remoteJid, {
            text: "Voice note process nahi ho saka — dobara bhej dein ya text likh dein."
          });
          return;
        }
      }

      if (!incomingText.trim()) return;

      console.log(`\n[WHATSAPP INBOUND] +${senderNumber}: "${incomingText}"`);

      // Forward to Python AI & Business Engine with authenticated bridge secret
      const res = await fetch(PYTHON_API_URL, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "X-Bridge-Secret": BRIDGE_SECRET
        },
        body: JSON.stringify({
          sender: senderNumber,
          message: incomingText
        })
      });

      if (res.ok) {
        const data = await res.json();
        const aiReply = data.reply;
        console.log(`[WHATSAPP OUTBOUND] Replying to +${senderNumber}...\n`);

        await sock.sendMessage(remoteJid, { text: aiReply });
      } else {
        console.error("Python API Error:", res.status, await res.text());
      }
    } catch (err) {
      console.error("Message handler error:", err);
    }
  });
}

startWhatsAppBot();
