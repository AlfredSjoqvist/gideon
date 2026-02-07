import os
import requests
import json
from dotenv import load_dotenv

load_dotenv()

def debug_telegram():
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    chat_id = os.getenv("TELEGRAM_CHAT_ID")
    
    print(f"--- Debugging Telegram ---")
    print(f"Token Found: {'Yes' if token else 'No'}")
    print(f"Chat ID: {chat_id}")

    url = f"https://api.telegram.org/bot{token}/sendMessage"
    
    # Try a very simple message with NO formatting first
    payload = {
        "chat_id": chat_id,
        "text": "Test message from Python - no HTML"
    }

    try:
        response = requests.post(url, data=payload)
        if response.status_code != 200:
            # This will show the actual error message from Telegram's servers
            print(f"❌ Server Response: {response.text}")
        response.raise_for_status()
        print("✅ Success! Check your phone.")
    except Exception as e:
        print(f"❌ Failed: {e}")

if __name__ == "__main__":
    debug_telegram()