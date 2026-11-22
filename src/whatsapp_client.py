import requests
from src.config import WHATSAPP_TOKEN, WHATSAPP_PHONE_NUMBER_ID

META_URL = f"https://graph.facebook.com/v21.0/{WHATSAPP_PHONE_NUMBER_ID}/messages"

def send_whatsapp_text(to_number: str, message: str) -> bool:
    """Sends a text message using Meta WhatsApp Cloud API."""
    if not WHATSAPP_TOKEN or not WHATSAPP_PHONE_NUMBER_ID:
        print(f"[MOCK WHATSAPP OUTGOING to {to_number}]:\n{message}\n")
        return True

    headers = {
        "Authorization": f"Bearer {WHATSAPP_TOKEN}",
        "Content-Type": "application/json"
    }

    payload = {
        "messaging_product": "whatsapp",
        "to": to_number,
        "type": "text",
        "text": {"body": message}
    }

    try:
        response = requests.post(META_URL, headers=headers, json=payload, timeout=10)
        if response.status_code == 200:
            return True
        else:
            print(f"Meta WhatsApp API Error {response.status_code}: {response.text}")
            return False
    except Exception as e:
        print(f"Failed to send WhatsApp message: {e}")
        return False
