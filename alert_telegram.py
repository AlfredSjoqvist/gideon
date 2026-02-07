import os
import requests
import base64
import time
from dotenv import load_dotenv
from google import genai
from google.genai import types

load_dotenv()

# Initialize Gemini Client
client = genai.Client(api_key=os.getenv("GEMINI_API_KEY"))
IMGBB_KEY = os.getenv("IMGBB_KEY")
# Pushcut Trigger URL: https://api.pushcut.io/[SECRET]/notifications/[NAME]
PUSHCUT_URL = os.getenv("PUSHCUT_TRIGGER_URL")

def generate_and_send_smart(title, hook, link):
    print("🎨 Generating AI Image...")
    
    # Loosen safety filters to avoid NoneType responses
    safety_settings = [
        types.SafetySetting(category="HARM_CATEGORY_HARASSMENT", threshold="BLOCK_NONE"),
        types.SafetySetting(category="HARM_CATEGORY_HATE_SPEECH", threshold="BLOCK_NONE"),
        types.SafetySetting(category="HARM_CATEGORY_SEXUALLY_EXPLICIT", threshold="BLOCK_NONE"),
        types.SafetySetting(category="HARM_CATEGORY_DANGEROUS_CONTENT", threshold="BLOCK_NONE"),
    ]

    response = client.models.generate_content(
        model="gemini-2.5-flash-image", 
        contents=[f"Professional editorial illustration: {title}. Context: {hook}"],
        config=types.GenerateContentConfig(
            response_modalities=["IMAGE"],
            safety_settings=safety_settings
        )
    )
    
    # Fix for the 'NoneType' / NameError issues
    if not response.candidates or not response.candidates[0].content:
        print("❌ Gemini blocked the response or returned nothing.")
        return

    # Correctly extracting 'img_data' from the response parts
    img_data = response.candidates[0].content.parts[0].inline_data.data
    b64_img = base64.b64encode(img_data).decode('utf-8')

    print("☁️ Uploading to ImgBB...")
    # Using POST with data= handles form-encoding correctly for ImgBB v1
    try:
        img_res = requests.post(
            "https://api.imgbb.com/1/upload",
            data={
                "key": IMGBB_KEY.strip(),
                "image": b64_img,
                "expiration": 600  # Deletes after 10 mins
            }
        ).json()
        
        if 'data' not in img_res:
            print(f"❌ ImgBB Error: {img_res}")
            return
            
        public_url = img_res['data']['url']
        print(f"✅ Image Live: {public_url}")

        print("🚀 Sending Pushcut Notification...")
        requests.post(PUSHCUT_URL, json={
            "title": title,
            "text": hook,
            "image": public_url,
            "defaultAction": {"url": link}
        })
        print("🔔 Done.")

    except Exception as e:
        print(f"❌ Pipeline Error: {e}")

if __name__ == "__main__":
    generate_and_send_smart(
        "AGI Solved!", 
        "Researchers at Stanford have allegedly cracked the final code.", 
        "https://www.google.com"
    )